"""
Jira Review Tracker Dashboard
Tracks review bounce-backs, time in states, and logged hours for Jira tickets.
"""

import streamlit as st
import pandas as pd

from jira_client import (
    get_issue,
    get_changelog,
    get_worklogs,
    extract_status_transitions,
    extract_description_text,
    extract_comments_text,
    JIRA_URL,
    JIRA_EMAIL,
    JIRA_API_TOKEN,
)
from utils import (
    parse_issue_keys_from_csv,
    parse_issue_keys_from_text,
    detect_bounce_backs,
    compute_time_in_states,
    total_worklog_hours,
    find_testim_references,
)

# ─── Page Configuration ───

st.set_page_config(
    page_title="Jira Review Tracker",
    page_icon=":mag:",
    layout="wide",
)

st.title("Jira Review Tracker")
st.caption(
    "Analyze review cycles for automated test script tickets — "
    "track bounce-backs, time spent in each state, and logged hours."
)

# ─── Sidebar ───

with st.sidebar:
    st.header("Connection")

    if not all([JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        st.error(
            "Jira credentials not configured. "
            "Set **JIRA_URL**, **JIRA_EMAIL**, and **JIRA_API_TOKEN** "
            "in your environment or Streamlit secrets."
        )
        st.stop()
    else:
        st.success(f"Connected to **{JIRA_URL}**")

    st.divider()
    st.subheader("Workflow Reference")
    st.code("To Do → In Progress → In Review → Done", language=None)
    st.markdown(
        "A **bounce-back** is detected whenever a ticket moves "
        "backward in the workflow (e.g. *In Review* → *In Progress*)."
    )

# ─── Section 1: Load Tickets ───

st.header("Load Tickets")

column_csv, column_text = st.columns(2)
issue_keys = []

with column_csv:
    st.subheader("Upload CSV Export")
    uploaded_file = st.file_uploader(
        "Upload a Jira CSV export",
        type=["csv"],
        help="The CSV file should contain a column named 'Key' or 'Issue key'.",
    )
    if uploaded_file is not None:
        csv_keys = parse_issue_keys_from_csv(uploaded_file.getvalue())
        if csv_keys:
            st.success(f"Found **{len(csv_keys)}** ticket(s) in the CSV file")
            issue_keys.extend(csv_keys)
        else:
            st.warning("No Jira issue keys found in the CSV. Please check the column names.")

with column_text:
    st.subheader("Paste Issue Keys")
    text_input = st.text_area(
        "Enter Jira issue keys (one per line or comma-separated)",
        placeholder="PROJ-123\nPROJ-456, PROJ-789",
        height=120,
    )
    if text_input.strip():
        text_keys = parse_issue_keys_from_text(text_input)
        if text_keys:
            st.success(f"Found **{len(text_keys)}** ticket(s)")
            issue_keys.extend(text_keys)

# Deduplicate preserving order
issue_keys = list(dict.fromkeys(issue_keys))

if not issue_keys:
    st.info("Upload a CSV file or paste issue keys above to get started.")
    st.stop()

st.divider()

# ─── Section 2: Fetch & Analyze ───

st.header("Ticket Analysis")

if st.button("Fetch and Analyze Tickets", type="primary", use_container_width=True):
    all_ticket_data = []
    progress_bar = st.progress(0, text="Fetching tickets...")

    for index, key in enumerate(issue_keys):
        progress_bar.progress(
            (index + 1) / len(issue_keys),
            text=f"Fetching {key} ({index + 1} of {len(issue_keys)})...",
        )

        issue = get_issue(key)
        if issue is None:
            st.warning(f"Could not fetch **{key}** — skipping")
            continue

        fields = issue.get("fields", {})
        changelog = get_changelog(key)
        worklogs = get_worklogs(key)

        transitions = extract_status_transitions(changelog)
        bounce_backs = detect_bounce_backs(transitions)
        time_in_states = compute_time_in_states(transitions, created_date=fields.get("created"))
        hours_logged = total_worklog_hours(worklogs)

        description_text = extract_description_text(fields.get("description"))
        comments = extract_comments_text(issue)
        testim_refs = find_testim_references(description_text, comments)

        assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
        current_status = (fields.get("status") or {}).get("name", "Unknown")

        all_ticket_data.append({
            "key": key,
            "summary": fields.get("summary", ""),
            "assignee": assignee,
            "status": current_status,
            "transitions": transitions,
            "bounce_backs": bounce_backs,
            "bounce_back_count": len(bounce_backs),
            "time_in_states": time_in_states,
            "hours_logged": hours_logged,
            "description": description_text,
            "testim_references": testim_refs,
        })

    progress_bar.empty()

    if not all_ticket_data:
        st.error("No tickets could be fetched. Please verify your credentials and issue keys.")
        st.stop()

    st.session_state["ticket_data"] = all_ticket_data

# ─── Display Results ───

if "ticket_data" not in st.session_state:
    st.stop()

data = st.session_state["ticket_data"]

# ─── Aggregate Metrics ───

st.subheader("Overview")

total_bounce_backs = sum(ticket["bounce_back_count"] for ticket in data)
average_bounce_backs = total_bounce_backs / len(data) if data else 0
total_hours_logged = sum(ticket["hours_logged"] for ticket in data)
average_review_hours = (
    sum(
        sum(
            period["duration_hours"]
            for period in ticket["time_in_states"]
            if period["status"].strip().lower() == "in review"
        )
        for ticket in data
    )
    / len(data)
    if data
    else 0
)

metric_col_1, metric_col_2, metric_col_3, metric_col_4 = st.columns(4)
metric_col_1.metric("Total Bounce-Backs", total_bounce_backs)
metric_col_2.metric("Average Bounce-Backs per Ticket", f"{average_bounce_backs:.1f}")
metric_col_3.metric("Total Hours Logged", f"{total_hours_logged:.1f} h")
metric_col_4.metric("Average Hours in Review", f"{average_review_hours:.1f} h")

# ─── Summary Table ───

st.subheader("Summary Table")

summary_rows = []
for ticket in data:
    review_hours = sum(
        period["duration_hours"]
        for period in ticket["time_in_states"]
        if period["status"].strip().lower() == "in review"
    )
    progress_hours = sum(
        period["duration_hours"]
        for period in ticket["time_in_states"]
        if period["status"].strip().lower() == "in progress"
    )
    summary_rows.append({
        "Issue Key": ticket["key"],
        "Title": ticket["summary"],
        "Assignee": ticket["assignee"],
        "Current Status": ticket["status"],
        "Bounce-Backs": ticket["bounce_back_count"],
        "Hours in Review": round(review_hours, 1),
        "Hours in Progress": round(progress_hours, 1),
        "Hours Logged": ticket["hours_logged"],
        "Testim References": len(ticket["testim_references"]),
    })

summary_df = pd.DataFrame(summary_rows)
st.dataframe(
    summary_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Issue Key": st.column_config.TextColumn("Issue Key", width="small"),
        "Bounce-Backs": st.column_config.NumberColumn("Bounce-Backs", format="%d"),
        "Hours in Review": st.column_config.NumberColumn("Hours in Review", format="%.1f"),
        "Hours in Progress": st.column_config.NumberColumn("Hours in Progress", format="%.1f"),
        "Hours Logged": st.column_config.NumberColumn("Hours Logged", format="%.1f"),
        "Testim References": st.column_config.NumberColumn("Testim References", format="%d"),
    },
)

st.divider()

# ─── Per-Ticket Details ───

st.subheader("Ticket Details")

for ticket in data:
    with st.expander(f"**{ticket['key']}** — {ticket['summary']}", expanded=False):

        # ── Info & Stats ──
        info_column, stats_column = st.columns([2, 1])

        with info_column:
            st.markdown(f"**Assignee:** {ticket['assignee']}")
            st.markdown(f"**Current Status:** `{ticket['status']}`")

            if ticket["testim_references"]:
                st.markdown("**Testim References:**")
                for reference in ticket["testim_references"]:
                    st.markdown(f"- `{reference}`")

        with stats_column:
            st.metric("Bounce-Backs", ticket["bounce_back_count"])
            st.metric("Hours Logged", f"{ticket['hours_logged']} h")

        # ── Status Transitions Timeline ──
        if ticket["transitions"]:
            st.markdown("**Status Transition History**")
            transition_rows = []
            for transition in ticket["transitions"]:
                transition_rows.append({
                    "Timestamp": transition["timestamp"][:19].replace("T", " "),
                    "From Status": transition["from_status"],
                    "To Status": transition["to_status"],
                    "Changed By": transition["author"],
                })
            st.dataframe(
                pd.DataFrame(transition_rows),
                use_container_width=True,
                hide_index=True,
            )

        # ── Time Spent in Each State ──
        if ticket["time_in_states"]:
            st.markdown("**Time Spent in Each State**")
            state_period_rows = []
            for period in ticket["time_in_states"]:
                entered = period["entered"][:19].replace("T", " ") if period["entered"] else ""
                exited = period["exited"][:19].replace("T", " ") if period["exited"] else ""
                state_period_rows.append({
                    "Status": period["status"],
                    "Entered": entered,
                    "Exited": exited,
                    "Duration (Hours)": period["duration_hours"],
                })
            st.dataframe(
                pd.DataFrame(state_period_rows),
                use_container_width=True,
                hide_index=True,
            )

        # ── Bounce-Back Events ──
        if ticket["bounce_backs"]:
            st.markdown("**Bounce-Back Events**")
            bounce_back_rows = []
            for event in ticket["bounce_backs"]:
                event_type = "Blocked" if event.get("is_blocked") else "Bounce-Back"
                bounce_back_rows.append({
                    "Event Type": event_type,
                    "Timestamp": event["timestamp"][:19].replace("T", " "),
                    "From Status": event["from_status"],
                    "To Status": event["to_status"],
                    "Changed By": event["author"],
                })
            st.dataframe(
                pd.DataFrame(bounce_back_rows),
                use_container_width=True,
                hide_index=True,
            )

        # ── Description ──
        if ticket["description"]:
            with st.popover("View Full Description"):
                st.markdown(ticket["description"][:3000])
