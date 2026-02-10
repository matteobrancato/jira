"""
Utility functions for CSV parsing, bounce-back detection,
time-in-state calculations, and Testim reference extraction.
"""

import re
import io
from datetime import datetime, timezone

import pandas as pd

# Workflow stages in order — lower index = earlier in pipeline
WORKFLOW_ORDER = ["to do", "in progress", "in review", "done"]

# States that represent a blocked/parked ticket (flagged separately from bounce-backs)
BLOCKED_STATES = {"blocked"}

# Regex for Jira issue keys (e.g. PROJ-123)
_JIRA_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9]+-\d+")
_JIRA_KEY_STRICT = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")

# Known column names for issue keys in Jira CSV exports
_CSV_KEY_COLUMNS = ["Key", "Issue key", "Issue Key", "issue_key", "key"]


def parse_issue_keys_from_csv(file_content: bytes) -> list[str]:
    """
    Parse a Jira CSV export and extract issue keys.
    Looks for standard key column names, falls back to first column.
    """
    dataframe = pd.read_csv(io.BytesIO(file_content))

    for column_name in _CSV_KEY_COLUMNS:
        if column_name in dataframe.columns:
            return dataframe[column_name].dropna().astype(str).str.strip().tolist()

    # Fallback: first column, filtered to values matching Jira key format
    first_column = dataframe.iloc[:, 0].dropna().astype(str).str.strip().tolist()
    return [value for value in first_column if _JIRA_KEY_STRICT.match(value)]


def parse_issue_keys_from_text(text: str) -> list[str]:
    """Extract Jira issue keys from free-form text, preserving order and removing duplicates."""
    return list(dict.fromkeys(_JIRA_KEY_PATTERN.findall(text)))


def parse_timestamp(timestamp_str: str) -> datetime:
    """Parse a Jira ISO timestamp into a timezone-aware datetime."""
    timestamp_str = timestamp_str.replace("Z", "+00:00")

    # Jira uses +0000 format (no colon) — convert to +00:00
    if re.search(r"[+-]\d{4}$", timestamp_str):
        timestamp_str = timestamp_str[:-2] + ":" + timestamp_str[-2:]

    return datetime.fromisoformat(timestamp_str)


def _workflow_index(status: str) -> int:
    """Return the position of a status in the workflow, or -1 if unknown/blocked."""
    normalized = status.strip().lower()
    if normalized in BLOCKED_STATES:
        return -1
    try:
        return WORKFLOW_ORDER.index(normalized)
    except ValueError:
        return -1


def detect_bounce_backs(transitions: list[dict]) -> list[dict]:
    """
    Detect backward movements in the workflow (bounce-backs)
    and transitions to blocked states.

    A bounce-back occurs when a ticket moves to an earlier workflow stage
    (e.g. In Review -> In Progress).
    """
    bounce_backs = []

    for transition in transitions:
        from_index = _workflow_index(transition["from_status"])
        to_index = _workflow_index(transition["to_status"])

        if from_index > 0 and to_index >= 0 and to_index < from_index:
            bounce_backs.append(transition)

        if transition["to_status"].strip().lower() in BLOCKED_STATES:
            blocked_event = dict(transition)
            blocked_event["is_blocked"] = True
            bounce_backs.append(blocked_event)

    return bounce_backs


def compute_time_in_states(
    transitions: list[dict],
    created_date: str | None = None,
) -> list[dict]:
    """
    Compute the duration spent in each state based on transition history.
    Returns a list of periods with status, entry/exit timestamps, and duration in hours.
    """
    if not transitions:
        return []

    periods = []
    first_transition_time = parse_timestamp(transitions[0]["timestamp"])
    creation_time = parse_timestamp(created_date) if created_date else first_transition_time

    # Period from creation to first transition (initial state)
    initial_status = transitions[0]["from_status"]
    if creation_time < first_transition_time:
        duration_hours = (first_transition_time - creation_time).total_seconds() / 3600
        periods.append({
            "status": initial_status,
            "entered": creation_time.isoformat(),
            "exited": first_transition_time.isoformat(),
            "duration_hours": round(duration_hours, 2),
        })

    # Each transition marks entry into a new state
    for index, transition in enumerate(transitions):
        entered = parse_timestamp(transition["timestamp"])

        if index + 1 < len(transitions):
            exited = parse_timestamp(transitions[index + 1]["timestamp"])
        else:
            exited = datetime.now(timezone.utc)

        duration_hours = (exited - entered).total_seconds() / 3600
        periods.append({
            "status": transition["to_status"],
            "entered": entered.isoformat(),
            "exited": exited.isoformat(),
            "duration_hours": round(duration_hours, 2),
        })

    return periods


def total_worklog_hours(worklogs: list[dict]) -> float:
    """Calculate total logged time in hours from worklog entries."""
    total_seconds = sum(entry.get("timeSpentSeconds", 0) for entry in worklogs)
    return round(total_seconds / 3600, 2)


# Patterns for detecting Testim references in text
_TESTIM_PATTERNS = [
    re.compile(r"https?://[^\s]*testim[^\s]*", re.IGNORECASE),
    re.compile(r"testim[:\s]+[\w\-]+", re.IGNORECASE),
    re.compile(r"test(?:im)?[\s\-_]*(?:id|name|link|url|ref)[\s:]+[\w\-/]+", re.IGNORECASE),
]


def find_testim_references(description_text: str, comments: list[str]) -> list[str]:
    """
    Search the issue description and comments for Testim references.
    Matches Testim URLs, test IDs, and keyword mentions.
    """
    references = []

    for text in [description_text] + comments:
        if not text:
            continue
        for pattern in _TESTIM_PATTERNS:
            references.extend(pattern.findall(text))

    # Deduplicate preserving order
    return list(dict.fromkeys(references))
