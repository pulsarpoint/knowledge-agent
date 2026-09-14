"""Deterministic Jira Markdown exports through the confined RAG file API."""

import hashlib
import re
from uuid import UUID

import httpx
from fastapi import HTTPException
from jira2markdown import convert
from pydantic import BaseModel, ConfigDict, Field


class TicketExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    folder: str = Field(default="jira", min_length=1, max_length=500,
                        pattern=r"^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$")
    connection_revision: int = Field(ge=1, strict=True)


def plain(value: object) -> str:
    """Keep Jira metadata on one line without introducing Markdown structure."""
    return re.sub(r"([\\`*_{}\[\]()<>#+!|])", r"\\\1", " ".join(str(value or "—").split()))


def ticket_markdown(base_url: str, issue: dict, comments: list[dict]) -> str:
    fields = issue["fields"]
    origin = hashlib.sha256(base_url.rstrip("/").encode()).hexdigest()
    lines = [f"<!-- knowledge-agent:jira-export:v1:{origin}:{issue['id']} -->", "",
             f"# {plain(issue['key'])}: {plain(fields['summary'])}", "",
             f"Source: [{issue['key']}]({base_url}/browse/{issue['key']})", "",
             f"- Issue ID: {plain(issue['id'])}", f"- Project: {plain(fields['project']['key'])}"]
    for label, key in [("Type", "issuetype"), ("Status", "status"), ("Priority", "priority")]:
        lines.append(f"- {label}: {plain((fields.get(key) or {}).get('name'))}")
    for label, key in [("Assignee", "assignee"), ("Reporter", "reporter")]:
        user = fields.get(key) or {}
        lines.append(f"- {label}: {plain(user.get('displayName') or user.get('name') or user.get('key'))}")
    lines += [f"- Labels: {plain(', '.join(fields.get('labels', [])))}",
              f"- Created: {plain(fields.get('created'))}", f"- Updated: {plain(fields['updated'])}",
              "", "## Description", "", convert(fields.get("description") or "No description."),
              "", f"## Comments ({len(comments)})", "",
              "All comments visible to the configured Jira user at export time."]
    for comment in comments:
        author = comment.get("author", {})
        lines += ["", f"### Comment {plain(comment['id'])}", "",
                  f"- Author: {plain(author.get('displayName') or author.get('name') or author.get('key'))}",
                  f"- Created: {plain(comment['created'])}", f"- Updated: {plain(comment['updated'])}",
                  "", convert(comment["body"])]
    return "\n".join(lines).rstrip() + "\n"


def publish_ticket(client: httpx.Client, key: str, project: UUID, actor: UUID,
                   folder: str, issue_key: str, content: str) -> dict:
    prefix = f"/backoffice/projects/{project}/"
    headers = {"x-rag-key": key, "x-rag-actor": str(actor)}

    def request(method: str, resource: str, **kwargs: object) -> dict:
        try:
            response = client.request(method, prefix + resource, headers=headers, timeout=30, **kwargs)
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid RAG response")
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "RAG service is unavailable. Retry the export to check the file") from None
        if response.status_code >= 300:
            message = result.get("error", {}).get("message") or "RAG could not save the ticket"
            raise HTTPException(response.status_code, message)
        return result

    for attempt in range(3):
        tree = request("GET", "tree")
        target = next((entry for entry in tree["folders"] if entry["path"] == folder), None)
        if target is not None and target["present"]:
            break
        try:
            target = request("POST", "folders", json={"path": folder,
                "description": target["description"] if target is not None else "Jira tickets and comments",
                "revision": tree["revision"]})
            break
        except HTTPException as error:
            if error.status_code != 409 or attempt == 2:
                raise
    path = f"{folder}/{issue_key}.md"
    try:
        previous = request("GET", "file", params={"path": path})
    except HTTPException as error:
        if error.status_code != 404:
            raise
        previous = None
    if previous is not None:
        if previous["content"].partition("\n")[0] != content.partition("\n")[0]:
            raise HTTPException(409, f"{path} belongs to another source. Choose a different folder; the existing file was preserved")
        if previous["content"] == content:
            return {"path": path, "folder_id": target["id"], "status": "unchanged", "hash": previous["hash"]}
    saved = request("PUT", "file", json={"path": path, "content": content,
                                        "expected_hash": previous["hash"] if previous is not None else None})
    return {"path": path, "folder_id": target["id"], "status": "updated" if previous is not None else "created", "hash": saved["hash"]}
