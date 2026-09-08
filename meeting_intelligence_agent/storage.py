"""
Storage Layer (spec section 11, MVP = Excel).

Files:
  /data/CRM.xlsx
  /data/PMO.xlsx
  /data/Requirements.xlsx      (created empty, reserved for Version 2 Agile agent)
  /data/business_objects.jsonl (Common Business Object, spec section 9 -- source of truth)

Only records the human has explicitly Approved on the Review screen are
ever written here (spec section 15: "User approval before saving").
"""
import json
import os
import logging
from datetime import datetime
from typing import List

import pandas as pd

import config
from models import BusinessObject, CRMOutput, PMOOutput

logger = logging.getLogger("meeting_intelligence_agent")

CRM_COLUMNS = [
    "meeting_id", "date", "customer", "contacts", "opportunity",
    "business_problem", "requirements", "competitors", "budget", "timeline",
    "decision_maker", "next_actions", "lead_score", "lead_status", "confidence",
]

PMO_COLUMNS = [
    "meeting_id", "date", "project", "decisions", "risk_description", "risk_impact",
    "risk_owner", "risk_status", "issues", "dependencies", "action_task",
    "action_owner", "action_due_date", "change_requests", "confidence",
]

REQUIREMENTS_COLUMNS = ["meeting_id", "date", "title", "story", "acceptance_criteria"]


def ensure_storage() -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    _ensure_workbook(config.CRM_XLSX, CRM_COLUMNS)
    _ensure_workbook(config.PMO_XLSX, PMO_COLUMNS)
    _ensure_workbook(config.REQUIREMENTS_XLSX, REQUIREMENTS_COLUMNS)
    if not os.path.exists(config.BUSINESS_OBJECTS_JSONL):
        open(config.BUSINESS_OBJECTS_JSONL, "a", encoding="utf-8").close()


def _ensure_workbook(path: str, columns: List[str]) -> None:
    if not os.path.exists(path):
        pd.DataFrame(columns=columns).to_excel(path, index=False)


def _append_rows(path: str, columns: List[str], rows: List[dict]) -> None:
    if not rows:
        return
    existing = pd.read_excel(path) if os.path.exists(path) else pd.DataFrame(columns=columns)
    new_df = pd.DataFrame(rows, columns=columns)
    combined = pd.concat([existing, new_df], ignore_index=True)
    combined.to_excel(path, index=False)


def save_crm_record(meeting_id: str, meeting_date: str, crm: CRMOutput) -> None:
    row = {
        "meeting_id": meeting_id,
        "date": meeting_date,
        "customer": crm.customer,
        "contacts": "; ".join(f"{c.name} ({c.role}, {c.company})" for c in crm.contacts),
        "opportunity": crm.opportunity,
        "business_problem": "; ".join(crm.business_problem),
        "requirements": "; ".join(crm.requirements),
        "competitors": "; ".join(crm.competitors),
        "budget": crm.budget,
        "timeline": crm.timeline,
        "decision_maker": crm.decision_maker,
        "next_actions": "; ".join(crm.next_actions),
        "lead_score": crm.lead_score,
        "lead_status": crm.lead_status,
        "confidence": crm.confidence,
    }
    _append_rows(config.CRM_XLSX, CRM_COLUMNS, [row])
    logger.info("CRM record saved for meeting_id=%s", meeting_id)  # no customer content logged


def save_pmo_record(meeting_id: str, meeting_date: str, pmo: PMOOutput) -> None:
    rows = []
    if pmo.risks:
        for risk in pmo.risks:
            rows.append({
                "meeting_id": meeting_id, "date": meeting_date, "project": pmo.project,
                "decisions": "; ".join(pmo.decisions),
                "risk_description": risk.description, "risk_impact": risk.impact,
                "risk_owner": risk.owner, "risk_status": risk.status,
                "issues": "; ".join(pmo.issues), "dependencies": "; ".join(pmo.dependencies),
                "action_task": "", "action_owner": "", "action_due_date": "",
                "change_requests": "; ".join(pmo.change_requests), "confidence": pmo.confidence,
            })
    if pmo.action_items:
        for action in pmo.action_items:
            rows.append({
                "meeting_id": meeting_id, "date": meeting_date, "project": pmo.project,
                "decisions": "; ".join(pmo.decisions),
                "risk_description": "", "risk_impact": "", "risk_owner": "", "risk_status": "",
                "issues": "; ".join(pmo.issues), "dependencies": "; ".join(pmo.dependencies),
                "action_task": action.task, "action_owner": action.owner,
                "action_due_date": action.due_date,
                "change_requests": "; ".join(pmo.change_requests), "confidence": pmo.confidence,
            })
    if not rows:
        # No risks or actions extracted -- still record the project-level info as one row
        rows.append({
            "meeting_id": meeting_id, "date": meeting_date, "project": pmo.project,
            "decisions": "; ".join(pmo.decisions),
            "risk_description": "", "risk_impact": "", "risk_owner": "", "risk_status": "",
            "issues": "; ".join(pmo.issues), "dependencies": "; ".join(pmo.dependencies),
            "action_task": "", "action_owner": "", "action_due_date": "",
            "change_requests": "; ".join(pmo.change_requests), "confidence": pmo.confidence,
        })
    _append_rows(config.PMO_XLSX, PMO_COLUMNS, rows)
    logger.info("PMO record saved for meeting_id=%s", meeting_id)  # no project content logged


def save_business_object(bo: BusinessObject) -> None:
    with open(config.BUSINESS_OBJECTS_JSONL, "a", encoding="utf-8") as f:
        f.write(bo.model_dump_json() + "\n")
    logger.info("Business object saved for meeting_id=%s", bo.meeting_id)


def load_crm_df() -> pd.DataFrame:
    ensure_storage()
    return pd.read_excel(config.CRM_XLSX)


def load_pmo_df() -> pd.DataFrame:
    ensure_storage()
    return pd.read_excel(config.PMO_XLSX)


def load_business_objects() -> List[dict]:
    ensure_storage()
    records = []
    with open(config.BUSINESS_OBJECTS_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def new_meeting_id() -> str:
    return "MTG-" + datetime.now().strftime("%Y%m%d-%H%M%S")
