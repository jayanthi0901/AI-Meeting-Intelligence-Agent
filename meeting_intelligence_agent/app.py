"""
AI Meeting Intelligence Agent -- Version 1

Scope (per product spec, Version 1 + Dashboard pulled forward from Version 2):
  - Upload Teams transcript (.docx / .vtt / .txt) or paste text
  - Intent classifier
  - CRM Agent
  - PMO Agent
  - Review screen (edit / approve / reject)
  - Export to Excel
  - Executive Dashboard

Run with:
    streamlit run app.py
"""
import logging
import os

import streamlit as st

import config
import storage
import intent_classifier
import role_inference
import dashboard
from agents import crm_agent, pmo_agent
from models import BusinessObject, CRMOutput, PMOOutput, Contact, Risk, ActionItem
from transcript_processor import parse_uploaded_file, parse_pasted_text, format_turns_for_prompt, extract_spoken_text

# ---------- Setup ----------

os.makedirs(config.DATA_DIR, exist_ok=True)
logging.basicConfig(
    filename=config.APP_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("meeting_intelligence_agent")

storage.ensure_storage()

st.set_page_config(page_title="AI Meeting Intelligence Agent", layout="wide")

for key, default in [
    ("parsed_transcript", None),
    ("intent_result", None),
    ("crm_result", None),
    ("pmo_result", None),
    ("meeting_id", None),
    ("inferred_roles", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------- Sidebar ----------

st.sidebar.title("AI Meeting Intelligence Agent")
st.sidebar.caption("Version 1")
page = st.sidebar.radio("Navigate", ["Upload & Process", "Review", "Dashboard", "Team Roster"], key="nav_radio")
st.sidebar.divider()
st.sidebar.caption(f"Local LLM: `{config.OLLAMA_MODEL}` via {config.OLLAMA_HOST}")
st.sidebar.caption("All processing runs locally. No transcript data leaves this device.")


# ---------- Page: Upload & Process ----------

def page_upload():
    st.header("Upload & Process")
    st.write(
        "Upload a Microsoft Teams transcript, or paste transcript text directly. "
        "Supported files: " + ", ".join(config.SUPPORTED_UPLOAD_EXTENSIONS)
    )

    input_mode = st.radio("Input method", ["Upload file", "Paste text"], horizontal=True, key="input_mode_radio")

    parsed = None
    if input_mode == "Upload file":
        uploaded = st.file_uploader(
            "Meeting transcript file",
            type=[ext.lstrip(".") for ext in config.SUPPORTED_UPLOAD_EXTENSIONS],
            key="transcript_file_uploader",
        )
        if uploaded is not None:
            try:
                parsed = parse_uploaded_file(uploaded.name, uploaded.read())
            except Exception as exc:
                st.error(f"Could not parse file: {exc}")
    else:
        pasted = st.text_area("Paste transcript text here", height=280, key="paste_transcript_textarea")
        if pasted.strip():
            parsed = parse_pasted_text(pasted)

    if parsed is not None:
        st.success(f"Transcript parsed: {len(parsed.turns)} turns, {len(parsed.participants)} participants detected.")
        with st.expander("Preview parsed transcript"):
            st.write(f"**Participants:** {', '.join(parsed.participants) or 'none detected'}")
            st.text(format_turns_for_prompt(parsed.turns, max_chars=4000))
        st.session_state["parsed_transcript"] = parsed

    st.divider()

    disabled = st.session_state["parsed_transcript"] is None
    if st.button("Process Transcript", type="primary", disabled=disabled, key="process_transcript_button"):
        transcript = st.session_state["parsed_transcript"]

        # Native MS Teams .vtt exports carry no role information -- resolve
        # it from the configured team roster (unmatched names default to
        # Customer). This is a fast file lookup, not an LLM call.
        if any(t.role == "" for t in transcript.turns):
            roles = role_inference.infer_roles(transcript)
            role_inference.apply_roles(transcript, roles)
            st.session_state["inferred_roles"] = roles

        prompt_text = format_turns_for_prompt(transcript.turns)

        # Keyword-based, not an LLM call -- instant, no spinner needed.
        # Score against spoken content only, NOT the "Speaker (Role):"
        # labels -- otherwise a participant's role (e.g. "Customer") can
        # collide with a keyword and skew scoring by who's in the room
        # rather than what was actually discussed.
        intent_result = intent_classifier.classify(extract_spoken_text(transcript.turns))
        st.session_state["intent_result"] = intent_result

        confidences = {i.workflow: i.confidence for i in intent_result.intents}
        st.caption(
            "Intent scores — " + ", ".join(f"{i.workflow}: {i.confidence:.0%}" for i in intent_result.intents)
        )
        active_workflows = [
            wf for wf in config.V1_SUPPORTED_WORKFLOWS
            if confidences.get(wf, 0.0) >= config.INTENT_CONFIDENCE_THRESHOLD
        ]

        if not active_workflows:
            st.warning(
                "No CRM or PMO intent detected above the confidence threshold "
                f"({config.INTENT_CONFIDENCE_THRESHOLD}). Nothing to review."
            )
            st.session_state["crm_result"] = None
            st.session_state["pmo_result"] = None
        else:
            if "CRM" in active_workflows:
                with st.spinner("Running CRM Intelligence Agent..."):
                    st.session_state["crm_result"] = crm_agent.run(prompt_text, confidences.get("CRM", 0.0))
            else:
                st.session_state["crm_result"] = None

            if "PMO" in active_workflows:
                with st.spinner("Running PMO Intelligence Agent..."):
                    st.session_state["pmo_result"] = pmo_agent.run(prompt_text, confidences.get("PMO", 0.0))
            else:
                st.session_state["pmo_result"] = None

            st.session_state["meeting_id"] = storage.new_meeting_id()
            st.success("Processing complete. Go to the Review tab to check the results.")

        if st.session_state.get("inferred_roles"):
            with st.expander("Speaker roles (from team roster; unmatched names default to Customer)"):
                for name, role in st.session_state["inferred_roles"].items():
                    st.write(f"**{name}** — {role}")


# ---------- Page: Team Roster ----------

def page_team_roster():
    st.header("Team Roster")
    st.write(
        "Configure which internal team members map to which role. "
        "Any meeting participant whose name is **not** listed here is assumed to be a Customer. "
        "Matching ignores case and extra spaces."
    )

    roster = role_inference.load_roster_by_role()

    st.subheader("Current roster")
    if not roster or not any(roster.values()):
        st.info("No roster configured yet — everyone will default to **Customer**.")
    else:
        for role, names in roster.items():
            if names:
                st.write(f"**{role}:** {', '.join(names)}")

    st.divider()
    st.subheader("Add a team member")
    with st.form("add_roster_member_form", clear_on_submit=True):
        new_name = st.text_input("Full name (exactly as it appears in transcripts)", key="roster_new_name")
        new_role = st.selectbox("Role", config.KNOWN_ROLE_OPTIONS, key="roster_new_role")
        submitted = st.form_submit_button("Add")
        if submitted:
            if new_name.strip():
                roster.setdefault(new_role, [])
                if new_name.strip() not in roster[new_role]:
                    roster[new_role].append(new_name.strip())
                    role_inference.save_roster(roster)
                    st.success(f"Added **{new_name.strip()}** as **{new_role}**.")
                    st.rerun()
                else:
                    st.warning(f"{new_name.strip()} is already listed as {new_role}.")
            else:
                st.warning("Enter a name before adding.")

    st.divider()
    st.subheader("Remove a team member")
    flat = [(role, name) for role, names in roster.items() for name in names]
    if flat:
        options = [f"{name} ({role})" for role, name in flat]
        selected = st.selectbox("Select entry to remove", options, key="roster_remove_select")
        if st.button("Remove", key="roster_remove_button"):
            idx = options.index(selected)
            role, name = flat[idx]
            roster[role].remove(name)
            role_inference.save_roster(roster)
            st.success(f"Removed {name}.")
            st.rerun()
    else:
        st.caption("No entries to remove yet.")


# ---------- Page: Review ----------

def _edit_crm(crm: CRMOutput) -> CRMOutput:
    st.markdown("**CRM Insights**")
    customer = st.text_input("Company", crm.customer, key="crm_customer")
    opportunity = st.text_input("Opportunity", crm.opportunity, key="crm_opportunity")
    budget = st.text_input("Budget", crm.budget, key="crm_budget")
    timeline = st.text_input("Timeline", crm.timeline, key="crm_timeline")
    decision_maker = st.text_input("Decision Maker", crm.decision_maker, key="crm_dm")
    lead_status = st.text_input("Lead Status", crm.lead_status, key="crm_status")
    lead_score = st.slider("Lead Score", 0, 100, crm.lead_score, key="crm_score")
    business_problem = st.text_area("Business Problem (one per line)", "\n".join(crm.business_problem), key="crm_bp")
    requirements = st.text_area("Requirements (one per line)", "\n".join(crm.requirements), key="crm_req")
    competitors = st.text_area("Competitors (one per line)", "\n".join(crm.competitors), key="crm_comp")
    next_actions = st.text_area("Next Actions (one per line)", "\n".join(crm.next_actions), key="crm_actions")
    st.caption(f"Classifier confidence: {crm.confidence:.0%}")

    return CRMOutput(
        customer=customer,
        contacts=crm.contacts,
        opportunity=opportunity,
        business_problem=[l for l in business_problem.splitlines() if l.strip()],
        requirements=[l for l in requirements.splitlines() if l.strip()],
        competitors=[l for l in competitors.splitlines() if l.strip()],
        budget=budget,
        timeline=timeline,
        decision_maker=decision_maker,
        next_actions=[l for l in next_actions.splitlines() if l.strip()],
        lead_score=lead_score,
        lead_status=lead_status,
        confidence=crm.confidence,
    )


def _edit_pmo(pmo: PMOOutput) -> PMOOutput:
    st.markdown("**PMO Insights**")
    project = st.text_input("Project", pmo.project, key="pmo_project")
    decisions = st.text_area("Decisions (one per line)", "\n".join(pmo.decisions), key="pmo_decisions")
    issues = st.text_area("Issues (one per line)", "\n".join(pmo.issues), key="pmo_issues")
    dependencies = st.text_area("Dependencies (one per line)", "\n".join(pmo.dependencies), key="pmo_deps")
    change_requests = st.text_area("Change Requests (one per line)", "\n".join(pmo.change_requests), key="pmo_cr")
    st.caption(f"Classifier confidence: {pmo.confidence:.0%}")

    st.markdown("*Risks*")
    edited_risks = []
    for idx, risk in enumerate(pmo.risks or [Risk()]):
        c1, c2, c3, c4 = st.columns(4)
        desc = c1.text_input("Description", risk.description, key=f"risk_desc_{idx}")
        impact = c2.text_input("Impact", risk.impact, key=f"risk_impact_{idx}")
        owner = c3.text_input("Owner", risk.owner, key=f"risk_owner_{idx}")
        status = c4.selectbox("Status", ["Open", "Mitigated", "Closed"],
                               index=["Open", "Mitigated", "Closed"].index(risk.status) if risk.status in ["Open", "Mitigated", "Closed"] else 0,
                               key=f"risk_status_{idx}")
        if desc.strip():
            edited_risks.append(Risk(description=desc, impact=impact, owner=owner, status=status))

    st.markdown("*Action Items*")
    edited_actions = []
    for idx, action in enumerate(pmo.action_items or [ActionItem()]):
        c1, c2, c3 = st.columns(3)
        task = c1.text_input("Task", action.task, key=f"action_task_{idx}")
        owner = c2.text_input("Owner", action.owner, key=f"action_owner_{idx}")
        due = c3.text_input("Due Date", action.due_date, key=f"action_due_{idx}")
        if task.strip():
            edited_actions.append(ActionItem(task=task, owner=owner, due_date=due))

    return PMOOutput(
        project=project,
        decisions=[l for l in decisions.splitlines() if l.strip()],
        risks=edited_risks,
        issues=[l for l in issues.splitlines() if l.strip()],
        dependencies=[l for l in dependencies.splitlines() if l.strip()],
        action_items=edited_actions,
        change_requests=[l for l in change_requests.splitlines() if l.strip()],
        confidence=pmo.confidence,
    )


def page_review():
    st.header("Meeting Intelligence Review")

    crm_result = st.session_state["crm_result"]
    pmo_result = st.session_state["pmo_result"]
    transcript = st.session_state["parsed_transcript"]

    if crm_result is None and pmo_result is None:
        st.info("Nothing to review yet. Process a transcript first.")
        return

    edited_crm, edited_pmo = None, None
    col1, col2 = st.columns(2)
    with col1:
        if crm_result is not None:
            edited_crm = _edit_crm(crm_result)
        else:
            st.info("CRM workflow was not triggered for this meeting.")
    with col2:
        if pmo_result is not None:
            edited_pmo = _edit_pmo(pmo_result)
        else:
            st.info("PMO workflow was not triggered for this meeting.")

    st.divider()
    a1, a2, _ = st.columns([1, 1, 4])
    approve = a1.button("Approve", type="primary", key="approve_button")
    reject = a2.button("Reject", key="reject_button")

    if approve:
        meeting_id = st.session_state["meeting_id"] or storage.new_meeting_id()
        today = __import__("datetime").date.today().isoformat()

        if edited_crm is not None:
            storage.save_crm_record(meeting_id, today, edited_crm)
        if edited_pmo is not None:
            storage.save_pmo_record(meeting_id, today, edited_pmo)

        bo = BusinessObject(
            meeting_id=meeting_id,
            date=today,
            participants=transcript.participants if transcript else [],
            summary="",
            crm=edited_crm,
            pmo=edited_pmo,
        )
        storage.save_business_object(bo)

        st.success(f"Saved meeting {meeting_id} to storage.")
        st.session_state["crm_result"] = None
        st.session_state["pmo_result"] = None
        st.session_state["parsed_transcript"] = None
        st.session_state["intent_result"] = None
        st.session_state["meeting_id"] = None
        st.session_state["inferred_roles"] = None

    if reject:
        st.session_state["crm_result"] = None
        st.session_state["pmo_result"] = None
        st.session_state["parsed_transcript"] = None
        st.session_state["intent_result"] = None
        st.session_state["meeting_id"] = None
        st.session_state["inferred_roles"] = None
        st.warning("Meeting intelligence rejected and discarded. Nothing was saved.")


# ---------- Router ----------

if page == "Upload & Process":
    page_upload()
elif page == "Review":
    page_review()
elif page == "Dashboard":
    dashboard.render()
elif page == "Team Roster":
    page_team_roster()