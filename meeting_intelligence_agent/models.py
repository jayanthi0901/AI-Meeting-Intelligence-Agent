"""
Pydantic data models mirroring the JSON schemas defined in the spec:
  - Section 5: Intent Classification Agent output
  - Section 6: CRM Intelligence Agent output
  - Section 7: PMO Intelligence Agent output
  - Section 9: Common Business Object (source of truth)
"""
from datetime import datetime, date as date_type
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


# ---------- Schema-repair helpers ----------
# Local LLMs don't always follow the requested JSON shape exactly (e.g. they
# sometimes return an object where a plain string was expected, or vice
# versa). Rather than crash the whole extraction on a shape mismatch, these
# helpers coerce whatever came back into the expected shape.

def _stringify(item: Any) -> str:
    """Best-effort conversion of a list item into a single descriptive string."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("description", "text", "task", "item", "value", "name", "title"):
            if item.get(key):
                base = str(item[key])
                extras = {k: v for k, v in item.items() if k != key and v}
                if extras:
                    extra_str = ", ".join(f"{k}: {v}" for k, v in extras.items())
                    return f"{base} ({extra_str})"
                return base
        # No recognizable key -- fall back to a flat "key: value" join.
        joined = "; ".join(f"{k}: {v}" for k, v in item.items() if v)
        return joined or str(item)
    return str(item)


def _coerce_str_list(v: Any) -> Any:
    if not isinstance(v, list):
        return v
    return [_stringify(item) for item in v]


def _coerce_dict_list(v: Any, string_field: str) -> Any:
    """For fields expecting a list of objects (e.g. Risk, ActionItem, Contact):
    if the model returned plain strings instead, wrap each into a dict using
    string_field as the primary attribute."""
    if not isinstance(v, list):
        return v
    coerced = []
    for item in v:
        if isinstance(item, dict):
            coerced.append(item)
        else:
            coerced.append({string_field: str(item)})
    return coerced


# ---------- Section 5: Intent Classification ----------

class IntentResult(BaseModel):
    workflow: str  # "CRM" | "PMO" | "AGILE_REQUIREMENT" | "SUPPORT" | "MANAGEMENT"
    confidence: float = Field(ge=0.0, le=1.0)


class IntentClassification(BaseModel):
    meeting_type: str = "UNKNOWN"
    intents: List[IntentResult] = Field(default_factory=list)

    def workflows_above(self, threshold: float) -> List[str]:
        return [i.workflow for i in self.intents if i.confidence >= threshold]


# ---------- Section 6: CRM Intelligence Agent ----------

class Contact(BaseModel):
    name: str = ""
    role: str = ""
    company: str = ""


class CRMOutput(BaseModel):
    customer: str = ""
    contacts: List[Contact] = Field(default_factory=list)
    opportunity: str = ""
    business_problem: List[str] = Field(default_factory=list)
    requirements: List[str] = Field(default_factory=list)
    competitors: List[str] = Field(default_factory=list)
    budget: str = ""
    timeline: str = ""
    decision_maker: str = ""
    next_actions: List[str] = Field(default_factory=list)
    lead_score: int = Field(default=0, ge=0, le=100)
    lead_status: str = ""
    confidence: float = 0.0  # carried over from the intent classifier

    @field_validator("business_problem", "requirements", "competitors", "next_actions", mode="before")
    @classmethod
    def _coerce_string_lists(cls, v):
        return _coerce_str_list(v)

    @field_validator("contacts", mode="before")
    @classmethod
    def _coerce_contacts(cls, v):
        return _coerce_dict_list(v, "name")


# ---------- Section 7: PMO Intelligence Agent ----------

class Risk(BaseModel):
    description: str = ""
    impact: str = ""
    owner: str = ""
    status: str = "Open"


class ActionItem(BaseModel):
    task: str = ""
    owner: str = ""
    due_date: str = ""


class PMOOutput(BaseModel):
    project: str = ""
    decisions: List[str] = Field(default_factory=list)
    risks: List[Risk] = Field(default_factory=list)
    issues: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    action_items: List[ActionItem] = Field(default_factory=list)
    change_requests: List[str] = Field(default_factory=list)
    confidence: float = 0.0

    @field_validator("decisions", "issues", "dependencies", "change_requests", mode="before")
    @classmethod
    def _coerce_string_lists(cls, v):
        return _coerce_str_list(v)

    @field_validator("risks", mode="before")
    @classmethod
    def _coerce_risks(cls, v):
        return _coerce_dict_list(v, "description")

    @field_validator("action_items", mode="before")
    @classmethod
    def _coerce_action_items(cls, v):
        return _coerce_dict_list(v, "task")


# ---------- Section 9: Common Business Object ----------

class BusinessObject(BaseModel):
    meeting_id: str
    date: str = Field(default_factory=lambda: date_type.today().isoformat())
    participants: List[str] = Field(default_factory=list)
    summary: str = ""
    crm: Optional[CRMOutput] = None
    pmo: Optional[PMOOutput] = None
    requirements: dict = Field(default_factory=dict)  # reserved for V2 Agile agent
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())