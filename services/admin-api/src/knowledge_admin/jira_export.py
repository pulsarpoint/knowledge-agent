"""Deterministic Jira Markdown exports through the confined RAG file API."""

import hashlib
import re
from uuid import UUID

import httpx
from jira2markdown import convert
from pydantic import BaseModel, ConfigDict, Field

from knowledge_admin.data_root import jira_folder
from knowledge_admin.rag_files import RagFiles



class TicketExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
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


def publish_ticket(client: httpx.Client, key: str, project: UUID, actor: UUID, issue_key: str, content: str) -> dict:
    """Write the ticket to <JIRA_FOLDER>/<KEY>.md; collections that hold that folder index it at their next sync."""
    return RagFiles(client, key, project, actor).write_markdown(f"{jira_folder()}/{issue_key}.md", content)
