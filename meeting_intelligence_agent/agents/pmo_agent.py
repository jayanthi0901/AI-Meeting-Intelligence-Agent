"""
PMO Intelligence Agent (spec section 7).

Converts meetings into project management artifacts, matching the PMO
output schema and generating the "Generated Artifacts" described in the
spec (RAID log, action tracker, decision register, status report).
"""
import logging

from llm_client import call_llm_json, LLMError
from models import PMOOutput

logger = logging.getLogger("meeting_intelligence_agent")

SYSTEM_PROMPT = """You are the PMO Intelligence Agent inside a meeting intelligence platform.
Read a project meeting transcript and extract project management intelligence.

Respond with ONLY a JSON object, no commentary, in exactly this shape:
{
  "project": "",
  "decisions": [],
  "risks": [{"description": "", "impact": "", "owner": "", "status": "Open"}],
  "issues": [],
  "dependencies": [],
  "action_items": [{"task": "", "owner": "", "due_date": ""}],
  "change_requests": []
}
status should be one of "Open", "Mitigated", "Closed".
due_date should be an ISO date (YYYY-MM-DD) if a date/timeframe was mentioned, otherwise an empty string.
Use empty strings/lists for anything not discussed in the transcript. Do not invent facts.
"""

# Explicit JSON Schema forcing the model to use these exact keys, rather than
# inventing its own structure -- observed in practice: a local model given
# only the system-prompt instructions above returned a completely different
# shape (grouped by speaker role) instead of this schema. A schema passed
# via Ollama's "format" field constrains sampling to these property names.
PMO_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "impact": {"type": "string"},
                    "owner": {"type": "string"},
                    "status": {"type": "string", "enum": ["Open", "Mitigated", "Closed"]},
                },
            },
        },
        "issues": {"type": "array", "items": {"type": "string"}},
        "dependencies": {"type": "array", "items": {"type": "string"}},
        "action_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "owner": {"type": "string"},
                    "due_date": {"type": "string"},
                },
            },
        },
        "change_requests": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "project", "decisions", "risks", "issues",
        "dependencies", "action_items", "change_requests",
    ],
}


def run(transcript_text: str, intent_confidence: float = 0.0) -> PMOOutput:
    prompt = f"Meeting transcript:\n---\n{transcript_text}\n---\n\nExtract the PMO intelligence now."
    try:
        raw = call_llm_json(prompt, system=SYSTEM_PROMPT, json_schema=PMO_JSON_SCHEMA)
        result = PMOOutput.model_validate(raw)
    except LLMError as exc:
        logger.error("PMO agent failed: %s", exc)
        result = PMOOutput(project="EXTRACTION_FAILED - needs manual review")
    result.confidence = intent_confidence
    return result