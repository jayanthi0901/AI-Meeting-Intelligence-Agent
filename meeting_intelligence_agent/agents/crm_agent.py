"""
CRM Intelligence Agent (spec section 6).

Converts meeting discussion into sales intelligence, matching the
CRM output schema and generating the "Generated Business Artifacts"
described in the spec (lead record, opportunity summary, requirements,
follow-up actions).
"""
import logging

from llm_client import call_llm_json, LLMError
from models import CRMOutput

logger = logging.getLogger("meeting_intelligence_agent")

SYSTEM_PROMPT = """You are the CRM Intelligence Agent inside a meeting intelligence platform.
Read a sales/customer meeting transcript and extract sales intelligence.

Respond with ONLY a JSON object, no commentary, in exactly this shape:
{
  "customer": "",
  "contacts": [{"name": "", "role": "", "company": ""}],
  "opportunity": "",
  "business_problem": [],
  "requirements": [],
  "competitors": [],
  "budget": "",
  "timeline": "",
  "decision_maker": "",
  "next_actions": [],
  "lead_score": 0,
  "lead_status": ""
}
lead_score is an integer 0-100 reflecting deal strength/readiness.
lead_status should be a short label such as "New", "Qualified", "Proposal", "Negotiation", "Closed Won", "Closed Lost".
Use empty strings/lists for anything not discussed in the transcript. Do not invent facts.
"""

# Explicit JSON Schema forcing the model to use these exact keys, rather than
# inventing its own structure (a real failure mode observed with local
# models even under plain format="json" mode -- see llm_client.py).
CRM_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "customer": {"type": "string"},
        "contacts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "company": {"type": "string"},
                },
            },
        },
        "opportunity": {"type": "string"},
        "business_problem": {"type": "array", "items": {"type": "string"}},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "competitors": {"type": "array", "items": {"type": "string"}},
        "budget": {"type": "string"},
        "timeline": {"type": "string"},
        "decision_maker": {"type": "string"},
        "next_actions": {"type": "array", "items": {"type": "string"}},
        "lead_score": {"type": "integer"},
        "lead_status": {"type": "string"},
    },
    "required": [
        "customer", "contacts", "opportunity", "business_problem", "requirements",
        "competitors", "budget", "timeline", "decision_maker", "next_actions",
        "lead_score", "lead_status",
    ],
}


def run(transcript_text: str, intent_confidence: float = 0.0) -> CRMOutput:
    prompt = f"Meeting transcript:\n---\n{transcript_text}\n---\n\nExtract the CRM intelligence now."
    try:
        raw = call_llm_json(prompt, system=SYSTEM_PROMPT, json_schema=CRM_JSON_SCHEMA)
        result = CRMOutput.model_validate(raw)
    except LLMError as exc:
        logger.error("CRM agent failed: %s", exc)
        result = CRMOutput(lead_status="EXTRACTION_FAILED - needs manual review")
    result.confidence = intent_confidence
    return result