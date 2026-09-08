"""
Intent Classification Agent (spec section 5) -- keyword-based (Version 1.3).

Local LLMs proved unreliable at this task in testing: given a schema asking
for CRM/PMO confidence scores, a small model can "fixate" and return the
same confidence for the same workflow regardless of what's actually in the
transcript (observed: CRM at a fixed 0.8 on every meeting, including
clearly PMO-only ones). Prompt tuning didn't fix it -- the model simply
isn't conditioning on transcript content reliably.

Following the same fix already applied to role inference, intent
classification is now a deterministic keyword scorer:
  - Count how many configured CRM/PMO phrases appear in the transcript
    (intent_keywords.json, next to app.py -- edit freely to tune).
  - More matches -> higher confidence, via simple tiered thresholds.
  - No LLM call: instant, transparent, and can't get "stuck" on one answer.

The previous LLM-based approach is kept below as `classify_llm` for
reference/optional use, but is not called by default.
"""
import json
import logging
import os
import re
from typing import Dict, List

import config
from llm_client import call_llm_json, LLMError
from models import IntentClassification, IntentResult

logger = logging.getLogger("meeting_intelligence_agent")

# Tiered count -> confidence mapping, mirroring the qualitative bands a
# human reviewer would use: a single passing mention shouldn't score the
# same as a workflow that dominates the meeting.
_COUNT_TO_CONFIDENCE = [
    (0, 0.0),
    (1, 0.3),
    (3, 0.5),
    (6, 0.7),
    (10, 0.9),
]


def _count_to_confidence(score: float) -> float:
    confidence = 1.0
    for threshold, band in _COUNT_TO_CONFIDENCE:
        if score <= threshold:
            confidence = band
            break
    else:
        confidence = 1.0
    return confidence


def load_keywords(path: str = None) -> Dict[str, List[str]]:
    """Loads intent_keywords.json. Fails soft to an empty dict (everything
    scores 0.0) if the file is missing or invalid, rather than crashing."""
    path = path or config.INTENT_KEYWORDS_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read intent keywords (%s): %s", path, exc)
        return {}
    return {workflow: phrases for workflow, phrases in raw.items() if isinstance(phrases, list)}


def save_keywords(keywords_by_workflow: Dict[str, List[str]], path: str = None) -> None:
    path = path or config.INTENT_KEYWORDS_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(keywords_by_workflow, f, indent=2, ensure_ascii=False)


def _count_matches(text: str, phrases: List[str]) -> float:
    """Returns a weighted match score, not a raw count. A specific
    multi-word phrase (e.g. "acceptance criteria") is a much stronger,
    less ambiguous signal than a generic single word (e.g. "customer"),
    so phrases are weighted by how many words they contain -- a 3-word
    phrase counts 3x as much as a single generic word match."""
    text_lower = text.lower()
    total = 0.0
    for phrase in phrases:
        phrase_lower = str(phrase).strip().lower()
        if not phrase_lower:
            continue
        weight = len(phrase_lower.split())
        # Word-boundary match so "sow" doesn't match inside "however", etc.
        pattern = r"\b" + re.escape(phrase_lower) + r"\b"
        occurrences = len(re.findall(pattern, text_lower))
        total += occurrences * weight
    return total


def classify(transcript_text: str) -> IntentClassification:
    """Scores CRM vs PMO confidence via deterministic keyword matching.
    This is a pure text scan -- no LLM call, so it's instant and can't
    produce the same answer regardless of content."""
    keywords = load_keywords()
    intents = []
    for workflow in config.V1_SUPPORTED_WORKFLOWS:  # ["CRM", "PMO"]
        phrases = keywords.get(workflow, [])
        count = _count_matches(transcript_text, phrases)
        confidence = _count_to_confidence(count)
        intents.append(IntentResult(workflow=workflow, confidence=confidence))
        logger.info("Intent scoring: workflow=%s keyword_matches=%d confidence=%.2f", workflow, count, confidence)

    top = max(intents, key=lambda i: i.confidence) if intents else None
    meeting_type = f"{top.workflow}_MEETING" if top and top.confidence > 0 else "UNCLASSIFIED_MEETING"

    return IntentClassification(meeting_type=meeting_type, intents=intents)


# ---------- Optional: LLM-based classification (kept for reference, not used by default) ----------

LLM_SYSTEM_PROMPT = """You are an Intent Classification Agent inside a meeting intelligence platform.
Read a meeting transcript and decide which downstream business workflows should process it.

Supported intent types:
- CRM: sales opportunity, customer requirement discussion
- PMO: risks, issues, actions, decisions on a project, user stories, acceptance criteria,
  feature requests, customer problems or issues being resolved, status reporting

Respond with ONLY a JSON object, no commentary, in exactly this shape:
{
  "meeting_type": "<one short label, e.g. CUSTOMER_MEETING, INTERNAL_STATUS_MEETING>",
  "intents": [
    {"workflow": "CRM", "confidence": 0.0},
    {"workflow": "PMO", "confidence": 0.0}
  ]
}
Confidence is a number between 0 and 1 reflecting how strongly that workflow applies.
Always include both workflow entries, even if confidence is 0.
"""

# Explicit JSON Schema forcing the model to use these exact keys, rather than
# inventing its own structure (a real failure mode observed with local
# models even under plain format="json" mode -- see llm_client.py). Even
# with this, a weak model can still ignore transcript content -- that's why
# classify() above is the default path.
LLM_INTENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "meeting_type": {"type": "string"},
        "intents": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "workflow": {"type": "string", "enum": ["CRM", "PMO"]},
                    "confidence": {"type": "number"},
                },
                "required": ["workflow", "confidence"],
            },
        },
    },
    "required": ["meeting_type", "intents"],
}


def classify_llm(transcript_text: str) -> IntentClassification:
    """Optional: ask the local LLM to classify instead of keyword scoring.
    Not called by default -- see module docstring for why. Fails soft to
    routing both workflows to human review if the LLM call fails."""
    prompt = f"Meeting transcript:\n---\n{transcript_text}\n---\n\nClassify this meeting now."
    try:
        raw = call_llm_json(prompt, system=LLM_SYSTEM_PROMPT, json_schema=LLM_INTENT_JSON_SCHEMA)
        return IntentClassification.model_validate(raw)
    except LLMError as exc:
        logger.error("LLM intent classification failed: %s", exc)
        return IntentClassification(
            meeting_type="UNKNOWN (classification failed)",
            intents=[
                {"workflow": "CRM", "confidence": 0.5},
                {"workflow": "PMO", "confidence": 0.5},
            ],
        )