"""
Jira PR Review Tracker Dashboard
Tracks bounce-backs, time in states, and review cycles for Jira tickets.
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


st.set_page_config(
    page_title="PR Review Tracker",
    page_icon=":bar_chart:",
    layout="wide",
)

st.title("PR Review Tracker")
st.caption("Track review bounce-backs, time in states, and logged hours for Jira tickets.")

# --- Sidebar: Configuration check ---
with st.sidebar:
    st.header("Configuration")
    if not all([JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN]):
        st.error("Missing Jira credentials. Set JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN in .env")
        st.stop()
    else:
        st.success(f"Connected to {JIRA_URL}")

    st.divider()
    st.markdown("**Workflow order:**")
    st.code("To Do → In Progress → In Review → Done", language=None)
    st.markdown("A *bounce-back* is any backward movement (e.g. In Review → In Progress).")

# --- Input section ---
st.header("1. Load Tickets")
col_csv, col_text = st.columns(2)

issue_keys = []

with col_csv:
    st.subheader("Upload CSV")
    uploaded_file = st.file_uploader(
        "Drop a Jira CSV export here",
        type=["csv"],
        help="CSV should contain a 'Key' or 'Issue key' column",
    )
    if uploaded_file is not None:
        csv_keys = parse_issue_keys_from_csv(uploaded_file.getvalue())
        if csv_keys:
            st.success(f"Found {len(csv_keys)} ticket(s) in CSV")
            issue_keys.extend(csv_keys)
        else:
            st.warning("No Jira issue keys found in CSV. Check column names.")

with col_text:
    st.subheader("Paste Issue Keys")
    text_input = st.text_area(
        "Enter Jira issue keys (one per line or comma-separated)",
        placeholder="PROJ-123\nPROJ-456, PROJ-789",
        height=120,
    )
    if text_input.strip():
        text_keys = parse_issue_keys_from_text(text_input)
        if text_keys:
            st.success(f"Found {len(text_keys)} ticket(s)")
            issue_keys.extend(text_keys)

# Deduplicate while preserving order
issue_keys = list(dict.fromkeys(issue_keys))

if not issue_keys:
    st.info("Upload a CSV or paste issue keys to get started.")
    st.stop()

st.divider()

# --- Fetch data ---
st.header("2. Ticket Analysis")

if st.button("Fetch & Analyze", type="primary", use_container_width=True):
    all_ticket_data = []
    progress = st.progress(0, text="Fetching tickets...")

    for i, key in enumerate(issue_keys):
        progress.progress((i + 1) / len(issue_keys), text=f"Fetching {key}...")

        issue = get_issue(key)
        if issue is None:
            st.warning(f"Could not fetch {key} – skipping")
            continue

        fields = issue.get("fields", {})
        changelog = get_changelog(key)
        worklogs = get_worklogs(key)
        transitions = extract_status_transitions(changelog)
        bounce_backs = detect_bounce_backs(transitions)
        time_in_states = compute_time_in_states(
            transitions,
            created_date=fields.get("created"),
        )
        hours_logged = total_worklog_hours(worklogs)

        description_text = extract_description_text(fields.get("description"))
        comments = extract_comments_text(issue)
        testim_refs = find_testim_references(description_text, comments)

        assignee = (fields.get("assignee") or {}).get("displayName", "Unassigned")
        status = (fields.get("status") or {}).get("name", "Unknown")

        all_ticket_data.append({
            "key": key,
            "summary": fields.get("summary", ""),
            "assignee": assignee,
            "status": status,
            "transitions": transitions,
            "bounce_backs": bounce_backs,
            "bounce_back_count": len(bounce_backs),
            "time_in_states": time_in_states,
            "hours_logged": hours_logged,
            "description": description_text,
            "testim_references": testim_refs,
        })

    progress.empty()

    if not all_ticket_data:
        st.error("No tickets could be fetched. Check your credentials and issue keys.")
        st.stop()

    st.session_state["ticket_data"] = all_ticket_data

# --- Display results ---
if "ticket_data" not in st.session_state:
    st.stop()

data = st.session_state["ticket_data"]

# --- Summary table ---
st.subheader("Summary")

summary_rows = []
for t in data:
    total_review_hours = sum(
        p["duration_hours"] for p in t["time_in_states"] if p["status"].strip().lower() == "in review"
    )
    total_progress_hours = sum(
        p["duration_hours"] for p in t["time_in_states"] if p["status"].strip().lower() == "in progress"
    )
    summary_rows.append({
        "Key": t["key"],
        "Title": t["summary"],
        "Assignee": t["assignee"],
        "Status": t["status"],
        "Bounce-backs": t["bounce_back_count"],
        "Hours in Review": round(total_review_hours, 1),
        "Hours in Progress": round(total_progress_hours, 1),
        "Hours Logged": t["hours_logged"],
        "Testim Refs": len(t["testim_references"]),
    })

summary_df = pd.DataFrame(summary_rows)
st.dataframe(
    summary_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Key": st.column_config.TextColumn("Key", width="small"),
        "Bounce-backs": st.column_config.NumberColumn("Bounce-backs", format="%d"),
        "Hours in Review": st.column_config.NumberColumn("Hrs in Review", format="%.1f"),
        "Hours in Progress": st.column_config.NumberColumn("Hrs in Progress", format="%.1f"),
        "Hours Logged": st.column_config.NumberColumn("Hrs Logged", format="%.1f"),
    },
)

# --- Aggregate metrics ---
col_m1, col_m2, col_m3, col_m4 = st.columns(4)
total_bounces = sum(t["bounce_back_count"] for t in data)
avg_bounces = total_bounces / len(data) if data else 0
total_logged = sum(t["hours_logged"] for t in data)
avg_review_hrs = (
    sum(
        sum(p["duration_hours"] for p in t["time_in_states"] if p["status"].strip().lower() == "in review")
        for t in data
    )
    / len(data)
    if data
    else 0
)

col_m1.metric("Total Bounce-backs", total_bounces)
col_m2.metric("Avg Bounce-backs / Ticket", f"{avg_bounces:.1f}")
col_m3.metric("Total Hours Logged", f"{total_logged:.1f}h")
col_m4.metric("Avg Hours in Review", f"{avg_review_hrs:.1f}h")

st.divider()

# --- Per-ticket detail ---
st.subheader("Ticket Details")

for ticket in data:
    with st.expander(f"**{ticket['key']}** – {ticket['summary']}", expanded=False):
        col_info, col_stats = st.columns([2, 1])

        with col_info:
            st.markdown(f"**Assignee:** {ticket['assignee']}")
            st.markdown(f"**Current Status:** `{ticket['status']}`")
            if ticket["testim_references"]:
                st.markdown("**Testim References:**")
                for ref in ticket["testim_references"]:
                    st.markdown(f"- `{ref}`")

        with col_stats:
            st.metric("Bounce-backs", ticket["bounce_back_count"])
            st.metric("Hours Logged", f"{ticket['hours_logged']}h")

        # Status transitions timeline
        if ticket["transitions"]:
            st.markdown("**Status Transitions:**")
            trans_rows = []
            for t in ticket["transitions"]:
                trans_rows.append({
                    "Timestamp": t["timestamp"][:19].replace("T", " "),
                    "From": t["from_status"],
                    "To": t["to_status"],
                    "By": t["author"],
                })
            st.dataframe(pd.DataFrame(trans_rows), use_container_width=True, hide_index=True)

        # Time in states
        if ticket["time_in_states"]:
            st.markdown("**Time in Each State:**")
            state_rows = []
            for p in ticket["time_in_states"]:
                entered = p["entered"][:19].replace("T", " ") if p["entered"] else ""
                exited = p["exited"][:19].replace("T", " ") if p["exited"] else ""
                state_rows.append({
                    "Status": p["status"],
                    "Entered": entered,
                    "Exited": exited,
                    "Duration (hours)": p["duration_hours"],
                })
            st.dataframe(pd.DataFrame(state_rows), use_container_width=True, hide_index=True)

        # Bounce-backs detail
        if ticket["bounce_backs"]:
            st.markdown("**Bounce-back Events:**")
            bb_rows = []
            for bb in ticket["bounce_backs"]:
                label = "BLOCKED" if bb.get("is_blocked") else "BOUNCE-BACK"
                bb_rows.append({
                    "Type": label,
                    "Timestamp": bb["timestamp"][:19].replace("T", " "),
                    "From": bb["from_status"],
                    "To": bb["to_status"],
                    "By": bb["author"],
                })
            st.dataframe(
                pd.DataFrame(bb_rows),
                use_container_width=True,
                hide_index=True,
            )

        # Description preview
        if ticket["description"]:
            with st.popover("View Description"):
                st.text(ticket["description"][:2000])
