"""
Jira Cloud REST API client.
Handles authentication and fetches issue details, changelogs, and worklogs.
"""

import os

import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str) -> str:
    """Read from Streamlit secrets (deploy) with fallback to .env (local)."""
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, "")


JIRA_URL = _get_secret("JIRA_URL").rstrip("/")
JIRA_EMAIL = _get_secret("JIRA_EMAIL")
JIRA_API_TOKEN = _get_secret("JIRA_API_TOKEN")

_AUTH = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)
_HEADERS = {"Accept": "application/json"}
_TIMEOUT = 30


def get_issue(issue_key: str) -> dict | None:
    """Fetch core issue fields: summary, status, assignee, description, comments."""
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,status,assignee,description,comment"}
    response = requests.get(url, headers=_HEADERS, auth=_AUTH, params=params, timeout=_TIMEOUT)
    if response.status_code == 200:
        return response.json()
    return None


def get_changelog(issue_key: str) -> list[dict]:
    """Fetch the full changelog for an issue, handling pagination."""
    all_histories = []
    start_at = 0

    while True:
        url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/changelog"
        params = {"startAt": start_at, "maxResults": 100}
        response = requests.get(url, headers=_HEADERS, auth=_AUTH, params=params, timeout=_TIMEOUT)

        if response.status_code != 200:
            break

        data = response.json()
        values = data.get("values", [])
        all_histories.extend(values)

        if start_at + len(values) >= data.get("total", 0):
            break
        start_at += len(values)

    return all_histories


def get_worklogs(issue_key: str) -> list[dict]:
    """Fetch all worklogs (time entries) for an issue."""
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/worklog"
    response = requests.get(url, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT)
    if response.status_code == 200:
        return response.json().get("worklogs", [])
    return []


def extract_status_transitions(changelog: list[dict]) -> list[dict]:
    """
    Extract status transitions from changelog entries.
    Returns a chronologically sorted list of transitions.
    """
    transitions = []

    for history in changelog:
        timestamp = history.get("created", "")
        author = (history.get("author") or {}).get("displayName", "Unknown")

        for item in history.get("items", []):
            if item.get("field") == "status":
                transitions.append({
                    "timestamp": timestamp,
                    "from_status": item.get("fromString", ""),
                    "to_status": item.get("toString", ""),
                    "author": author,
                })

    transitions.sort(key=lambda t: t["timestamp"])
    return transitions


def _extract_adf_text(node) -> str:
    """Recursively extract plain text from Jira's Atlassian Document Format."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node

    text_parts = []

    def walk(current):
        if isinstance(current, dict):
            if current.get("type") == "text":
                text_parts.append(current.get("text", ""))
            for child in current.get("content", []):
                walk(child)
        elif isinstance(current, list):
            for item in current:
                walk(item)

    walk(node)
    return " ".join(text_parts)


# Public alias kept for import compatibility
extract_description_text = _extract_adf_text


def extract_comments_text(issue_data: dict) -> list[str]:
    """Extract plain text from all comments on an issue."""
    comments = []
    comment_field = (issue_data.get("fields") or {}).get("comment", {})

    for comment in comment_field.get("comments", []):
        text = _extract_adf_text(comment.get("body"))
        if text:
            comments.append(text)

    return comments
