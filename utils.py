"""
Utility functions: CSV parsing, bounce-back detection, time calculations, Testim references.
"""

import re
import io
from datetime import datetime, timezone
import pandas as pd


# Workflow order – lower index = earlier in pipeline
WORKFLOW_ORDER = [
    "to do",
    "in progress",
    "in review",
    "done",
]

# States considered "blocked" / parked (not forward, not backward – just flagged)
BLOCKED_STATES = {"blocked"}


def parse_issue_keys_from_csv(file_content: bytes) -> list[str]:
    """
    Parse a CSV export and extract Jira issue keys.
    Looks for columns named 'Key', 'Issue key', 'Issue Key', or 'issue_key'.
    Falls back to first column if no match.
    """
    df = pd.read_csv(io.BytesIO(file_content))
    key_columns = ["Key", "Issue key", "Issue Key", "issue_key", "key"]
    for col in key_columns:
        if col in df.columns:
            return df[col].dropna().astype(str).str.strip().tolist()
    # Fallback: first column
    first_col = df.iloc[:, 0].dropna().astype(str).str.strip().tolist()
    # Filter to things that look like Jira keys (PROJ-123)
    pattern = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
    return [k for k in first_col if pattern.match(k)]


def parse_issue_keys_from_text(text: str) -> list[str]:
    """Extract Jira issue keys from free-form text input."""
    pattern = re.compile(r"[A-Z][A-Z0-9]+-\d+")
    return list(dict.fromkeys(pattern.findall(text)))


def parse_timestamp(ts: str) -> datetime:
    """Parse Jira ISO timestamp to datetime."""
    # Jira format: 2024-01-15T10:30:00.000+0000
    ts = ts.replace("Z", "+00:00")
    # Handle +0000 style offset (no colon)
    if re.search(r"[+-]\d{4}$", ts):
        ts = ts[:-2] + ":" + ts[-2:]
    return datetime.fromisoformat(ts)


def _workflow_index(status: str) -> int:
    """Return index in workflow or -1 if unknown."""
    normalized = status.strip().lower()
    if normalized in BLOCKED_STATES:
        return -1
    for i, s in enumerate(WORKFLOW_ORDER):
        if normalized == s:
            return i
    return -1


def detect_bounce_backs(transitions: list[dict]) -> list[dict]:
    """
    Detect bounce-backs: any transition that moves backward in the workflow.
    For example: In Review → In Progress is a bounce-back.
    Returns list of bounce-back transitions with extra metadata.
    """
    bounce_backs = []
    for t in transitions:
        from_idx = _workflow_index(t["from_status"])
        to_idx = _workflow_index(t["to_status"])
        # A bounce-back is when we go to a lower workflow index
        # (and both states are known in the workflow)
        if from_idx > 0 and to_idx >= 0 and to_idx < from_idx:
            bounce_backs.append(t)
        # Also flag: anything → Blocked as notable (not a bounce-back per se)
        to_lower = t["to_status"].strip().lower()
        if to_lower in BLOCKED_STATES:
            bb = dict(t)
            bb["is_blocked"] = True
            bounce_backs.append(bb)
    return bounce_backs


def compute_time_in_states(transitions: list[dict], created_date: str | None = None) -> list[dict]:
    """
    Compute how long the issue spent in each state.
    Returns: [{"status": ..., "entered": ..., "exited": ..., "duration_hours": ...}, ...]
    """
    if not transitions:
        return []

    periods = []
    # Initial state: from issue creation to first transition
    first_transition_time = parse_timestamp(transitions[0]["timestamp"])
    if created_date:
        creation_time = parse_timestamp(created_date)
    else:
        creation_time = first_transition_time

    initial_status = transitions[0]["from_status"]
    if creation_time < first_transition_time:
        delta = (first_transition_time - creation_time).total_seconds() / 3600
        periods.append({
            "status": initial_status,
            "entered": creation_time.isoformat(),
            "exited": first_transition_time.isoformat(),
            "duration_hours": round(delta, 2),
        })

    # Each transition defines: entered new state at transition time
    for i, t in enumerate(transitions):
        entered = parse_timestamp(t["timestamp"])
        if i + 1 < len(transitions):
            exited = parse_timestamp(transitions[i + 1]["timestamp"])
        else:
            exited = datetime.now(timezone.utc)
        delta = (exited - entered).total_seconds() / 3600
        periods.append({
            "status": t["to_status"],
            "entered": entered.isoformat(),
            "exited": exited.isoformat(),
            "duration_hours": round(delta, 2),
        })

    return periods


def total_worklog_hours(worklogs: list[dict]) -> float:
    """Sum all worklog time in hours."""
    total_seconds = sum(w.get("timeSpentSeconds", 0) for w in worklogs)
    return round(total_seconds / 3600, 2)


def find_testim_references(description_text: str, comments: list[str]) -> list[str]:
    """
    Search description and comments for Testim references.
    Looks for Testim URLs, test IDs, and mentions.
    """
    references = []
    all_text = [description_text] + comments

    patterns = [
        # Testim URLs
        re.compile(r"https?://[^\s]*testim[^\s]*", re.IGNORECASE),
        # Testim test IDs (common format)
        re.compile(r"testim[:\s]+[\w\-]+", re.IGNORECASE),
        # Generic testim mention with context
        re.compile(r"(?:test(?:im)?[\s\-_]*(?:id|name|link|url|ref)[\s:]+[\w\-/]+)", re.IGNORECASE),
    ]

    for text in all_text:
        if not text:
            continue
        for pattern in patterns:
            matches = pattern.findall(text)
            references.extend(matches)

    return list(dict.fromkeys(references))  # deduplicate preserving order
