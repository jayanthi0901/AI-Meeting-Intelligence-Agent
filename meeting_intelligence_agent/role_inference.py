"""
Role Inference (Version 1.2 -- roster-based).

Native Microsoft Teams .vtt exports carry no role information (just a
display name), and asking a small local LLM to *guess* each person's role
from conversation tone turned out to be unreliable -- it frequently returns
"Unknown" for everyone, especially with smaller/faster local models.

Instead, roles are now resolved deterministically from a configurable team
roster file (team_roster.json, next to app.py):

    {
      "Project Manager": ["Jayanthi Palanisamy"],
      "Developer": ["Angel Palacios", "Priya Nandan"],
      "Sales Representative": ["Sarah Mitchell"]
    }

Any transcript participant whose name is NOT found in the roster is assumed
to be a Customer. Matching is case-insensitive and whitespace-tolerant, so
"angel palacios" and "Angel  Palacios" both match "Angel Palacios".

The previous LLM-based approach is kept below as `infer_roles_llm` for
optional use, but is not called by default.
"""
import json
import logging
import os
from typing import Dict, List

import config
from llm_client import call_llm_json, LLMError
from transcript_processor import ParsedTranscript, format_turns_for_prompt

logger = logging.getLogger("meeting_intelligence_agent")

DEFAULT_ROLE = "Customer"


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def load_roster_by_role(path: str = None) -> Dict[str, List[str]]:
    """Returns the roster in its native {role: [names]} shape, for display/editing.
    Returns an empty dict if the file is missing or invalid (fails soft)."""
    path = path or config.TEAM_ROSTER_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read team roster (%s): %s", path, exc)
        return {}
    # Drop any non-list values (e.g. a "_comment" string key) so callers
    # always get a clean {role: [names]} shape.
    return {role: names for role, names in raw.items() if isinstance(names, list)}


def save_roster(roster_by_role: Dict[str, List[str]], path: str = None) -> None:
    path = path or config.TEAM_ROSTER_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(roster_by_role, f, indent=2, ensure_ascii=False)


def _flat_lookup(path: str = None) -> Dict[str, str]:
    """Builds a {normalized_name: role} lookup from the roster file."""
    lookup: Dict[str, str] = {}
    for role, names in load_roster_by_role(path).items():
        for name in names:
            lookup[_normalize(str(name))] = role
    return lookup


def infer_roles(transcript: ParsedTranscript) -> Dict[str, str]:
    """Resolves each participant's role from the configured team roster.
    Anyone not found in the roster is assumed to be a Customer. This is a
    pure file lookup -- no LLM call, so it's instant and 100% deterministic."""
    lookup = _flat_lookup()
    return {name: lookup.get(_normalize(name), DEFAULT_ROLE) for name in transcript.participants}


def apply_roles(transcript: ParsedTranscript, roles: Dict[str, str]) -> None:
    """Back-fills the `role` field on any turn that doesn't already have one.
    Turns that already carry an explicit role (e.g. from a '(Role)' tag in
    the source transcript) are left untouched."""
    for turn in transcript.turns:
        if not turn.role and turn.speaker in roles:
            turn.role = roles[turn.speaker]


# ---------- Optional: LLM-based inference (kept for reference, not used by default) ----------

LLM_SYSTEM_PROMPT = """You are a Role Inference Agent inside a meeting intelligence platform.
You are given a meeting transcript where each speaker's name is known but their role is not.
Based on what each person says and how they participate, infer their most likely role.

Use one of these role labels wherever it clearly fits, otherwise use "Unknown":
- Customer
- Project Manager
- Developer
- Business Analyst
- Executive Sponsor
- Sales Representative
- QA/Tester
- Other

Do not guess randomly -- use "Unknown" if the transcript doesn't give enough signal.

Respond with ONLY a JSON object, no commentary, in exactly this shape:
{
  "roles": {
    "<exact speaker name as given>": "<role label>"
  }
}
Include every participant listed below exactly once, using their name exactly as given.
"""


def infer_roles_llm(transcript: ParsedTranscript) -> Dict[str, str]:
    """Optional fallback: ask the local LLM to guess roles from conversation
    content instead of defaulting unmatched names to Customer. Not called by
    default -- kept here in case a future version wants to blend roster
    lookup with LLM guessing for names the roster doesn't cover. Fails soft
    to 'Unknown' for everyone if the LLM call fails."""
    participants = transcript.participants
    if not participants:
        return {}
    prompt = (
        f"Participants: {', '.join(participants)}\n\n"
        f"Transcript:\n---\n{format_turns_for_prompt(transcript.turns)}\n---\n\n"
        f"Infer each participant's role now."
    )
    try:
        raw = call_llm_json(prompt, system=LLM_SYSTEM_PROMPT)
        roles = raw.get("roles", {})
        if not isinstance(roles, dict):
            raise LLMError("Role inference response did not contain a 'roles' object.")
        return {name: str(roles.get(name, "Unknown")) for name in participants}
    except LLMError as exc:
        logger.error("LLM role inference failed: %s", exc)
        return {name: "Unknown" for name in participants}
