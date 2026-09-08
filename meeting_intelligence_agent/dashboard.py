"""
Executive Dashboard (spec section 12).

Note: the spec places the Dashboard in Version 2 scope, but it is
included here in Version 1 per explicit request. It reads only from the
already-approved Excel storage layer (CRM.xlsx / PMO.xlsx) -- nothing
here talks to the LLM.
"""
import pandas as pd
import plotly.express as px
import streamlit as st

import storage


def _metric_row(crm_df: pd.DataFrame, pmo_df: pd.DataFrame) -> None:
    meetings_processed = len(
        set(crm_df["meeting_id"]).union(set(pmo_df["meeting_id"]))
    )
    opportunities_created = crm_df["opportunity"].fillna("").astype(str).str.strip().ne("").sum() if not crm_df.empty else 0
    risks_identified = pmo_df["risk_description"].fillna("").astype(str).str.strip().ne("").sum() if not pmo_df.empty else 0

    open_actions = 0
    overdue_actions = 0
    if not pmo_df.empty and "action_task" in pmo_df.columns:
        actions = pmo_df[pmo_df["action_task"].fillna("").astype(str).str.strip() != ""]
        open_actions = len(actions)
        if "action_due_date" in actions.columns:
            due = pd.to_datetime(actions["action_due_date"], errors="coerce")
            overdue_actions = int((due < pd.Timestamp.now().normalize()).sum())

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Meetings Processed", meetings_processed)
    col2.metric("Opportunities Created", int(opportunities_created))
    col3.metric("Risks Identified", int(risks_identified))
    col4.metric("Open Actions", open_actions)
    col5.metric("Overdue Actions", overdue_actions)


def _crm_charts(crm_df: pd.DataFrame) -> None:
    st.subheader("CRM")
    if crm_df.empty:
        st.info("No approved CRM records yet.")
        return

    c1, c2 = st.columns(2)

    with c1:
        status_counts = crm_df["lead_status"].fillna("Unspecified").replace("", "Unspecified").value_counts().reset_index()
        status_counts.columns = ["lead_status", "count"]
        fig = px.bar(status_counts, x="lead_status", y="count", title="Leads by Status")
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        by_customer = crm_df.copy()
        by_customer["customer"] = by_customer["customer"].fillna("Unspecified").replace("", "Unspecified")
        opp_counts = by_customer.groupby("customer").size().reset_index(name="opportunities")
        fig = px.bar(opp_counts, x="customer", y="opportunities", title="Opportunities by Customer")
        st.plotly_chart(fig, use_container_width=True)

    # Pipeline value: parse numeric budget strings where possible
    budgets = crm_df["budget"].astype(str).str.extract(r"([\d,.]+)")[0]
    budgets = pd.to_numeric(budgets.str.replace(",", ""), errors="coerce")
    total_pipeline = budgets.sum()
    st.metric("Pipeline Value (parsed from budget field)", f"${total_pipeline:,.0f}" if pd.notna(total_pipeline) else "N/A")


def _pmo_charts(pmo_df: pd.DataFrame) -> None:
    st.subheader("PMO")
    if pmo_df.empty:
        st.info("No approved PMO records yet.")
        return

    c1, c2 = st.columns(2)

    with c1:
        risk_rows = pmo_df[pmo_df["risk_description"].fillna("").astype(str).str.strip() != ""]
        if not risk_rows.empty:
            open_risks = risk_rows["risk_status"].fillna("Open").replace("", "Open").value_counts().reset_index()
            open_risks.columns = ["status", "count"]
            fig = px.pie(open_risks, names="status", values="count", title="Open Risks by Status")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No risks recorded yet.")

    with c2:
        action_rows = pmo_df[pmo_df["action_task"].fillna("").astype(str).str.strip() != ""]
        if not action_rows.empty:
            due = pd.to_datetime(action_rows["action_due_date"], errors="coerce")
            completion = pd.DataFrame({
                "status": ["Overdue" if pd.notna(d) and d < pd.Timestamp.now().normalize() else "On Track" for d in due]
            })
            comp_counts = completion["status"].value_counts().reset_index()
            comp_counts.columns = ["status", "count"]
            fig = px.pie(comp_counts, names="status", values="count", title="Action Completion Outlook")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No action items recorded yet.")

    issue_rows = pmo_df[pmo_df["issues"].fillna("").astype(str).str.strip() != ""]
    if not issue_rows.empty:
        trend = issue_rows.groupby("date").size().reset_index(name="issues_logged")
        fig = px.line(trend, x="date", y="issues_logged", title="Issue Trend Over Time", markers=True)
        st.plotly_chart(fig, use_container_width=True)


def render() -> None:
    st.header("Executive Dashboard")
    st.caption("Reflects only meetings that have been reviewed and approved.")

    crm_df = storage.load_crm_df()
    pmo_df = storage.load_pmo_df()

    if crm_df.empty and pmo_df.empty:
        st.info("No approved meeting data yet. Process and approve a transcript to populate the dashboard.")
        return

    _metric_row(crm_df, pmo_df)
    st.divider()
    _crm_charts(crm_df)
    st.divider()
    _pmo_charts(pmo_df)
