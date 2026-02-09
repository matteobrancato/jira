"""
Jira Cloud REST API client for fetching issue details, changelogs, and worklogs.
"""

import os
import requests
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

load_dotenv()


def _get_secret(key: str) -> str:
    """Read from Streamlit secrets (deploy) or .env (local)."""
    try:
        import streamlit as st
        return st.secrets.get(key, os.getenv(key, ""))
    except Exception:
        return os.getenv(key, "")


JIRA_URL = _get_secret("JIRA_URL").rstrip("/")
JIRA_EMAIL = _get_secret("JIRA_EMAIL")
JIRA_API_TOKEN = _get_secret("JIRA_API_TOKEN")


def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)


def _headers() -> dict:
    return {"Accept": "application/json"}


def get_issue(issue_key: str) -> dict | None:
    """Fetch core issue fields: summary, status, assignee, description."""
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,status,assignee,description,comment"}
    resp = requests.get(url, headers=_headers(), auth=_auth(), params=params, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return None


def get_changelog(issue_key: str) -> list[dict]:
    """Fetch full changelog for an issue (status transitions with timestamps)."""
    histories = []
    start_at = 0
    while True:
        url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/changelog"
        params = {"startAt": start_at, "maxResults": 100}
        resp = requests.get(url, headers=_headers(), auth=_auth(), params=params, timeout=30)
        if resp.status_code != 200:
            break
        data = resp.json()
        values = data.get("values", [])
        histories.extend(values)
        if start_at + len(values) >= data.get("total", 0):
            break
        start_at += len(values)
    return histories


def get_worklogs(issue_key: str) -> list[dict]:
    """Fetch worklogs (time logged) for an issue."""
    url = f"{JIRA_URL}/rest/api/3/issue/{issue_key}/worklog"
    resp = requests.get(url, headers=_headers(), auth=_auth(), timeout=30)
    if resp.status_code == 200:
        return resp.json().get("worklogs", [])
    return []


def extract_status_transitions(changelog: list[dict]) -> list[dict]:
    """
    Parse changelog and return a list of status transitions:
    [{"timestamp": ..., "from": ..., "to": ..., "author": ...}, ...]
    """
    transitions = []
    for history in changelog:
        created = history.get("created", "")
        author = (history.get("author") or {}).get("displayName", "Unknown")
        for item in history.get("items", []):
            if item.get("field") == "status":
                transitions.append({
                    "timestamp": created,
                    "from_status": item.get("fromString", ""),
                    "to_status": item.get("toString", ""),
                    "author": author,
                })
    transitions.sort(key=lambda t: t["timestamp"])
    return transitions


def extract_description_text(description: dict | None) -> str:
    """
    Recursively extract plain text from Jira's Atlassian Document Format (ADF).
    """
    if description is None:
        return ""
    if isinstance(description, str):
        return description

    text_parts = []

    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                text_parts.append(node.get("text", ""))
            for child in node.get("content", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(description)
    return " ".join(text_parts)


def extract_comments_text(issue_data: dict) -> list[str]:
    """Extract plain text from issue comments."""
    comments = []
    comment_field = (issue_data.get("fields") or {}).get("comment", {})
    for c in comment_field.get("comments", []):
        body = c.get("body")
        text = extract_description_text(body)
        if text:
            comments.append(text)
    return comments
