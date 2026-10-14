"""Bounded Jira Data Center REST calls with PAT authentication."""

import json
import re
import time
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

JQL = "ORDER BY updated DESC, key DESC"
ISSUE_FIELDS = ["summary", "description", "project", "issuetype", "status", "priority", "assignee", "reporter", "labels", "created", "updated"]


class JiraSettingsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    base_url: str = Field(min_length=1, max_length=1000)
    username: str = Field(min_length=1, max_length=200)
    token: SecretStr | None = None
    revision: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            valid = (url.scheme in {"http", "https"} and url.hostname is not None
                     and url.username is None and url.password is None and not url.query and not url.fragment
                     and not any(char.isspace() or ord(char) < 32 for char in value)
                     and re.fullmatch(r"[A-Za-z0-9._~/-]*", url.path) is not None
                     and not any(part in {".", ".."} for part in url.path.split("/"))
                     and "\\" not in value and "//" not in url.path)
            if url.port is not None and not 1 <= url.port <= 65535:
                valid = False
            normalized = str(httpx.URL(value)).rstrip("/")
        except (ValueError, httpx.InvalidURL):
            valid = False
            normalized = ""
        if not valid:
            raise ValueError("Enter an HTTP(S) Jira base URL without credentials, query parameters, or fragments")
        return normalized

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and (not 1 <= len(value.get_secret_value()) <= 4096
                or not all(33 <= ord(char) <= 126 for char in value.get_secret_value())):
            raise ValueError("Enter a personal access token without whitespace")
        return value


class TicketFetchInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    jql: str = Field(default=JQL, min_length=1, max_length=4000)
    limit: int = Field(default=25, ge=1, le=200, strict=True)


class JiraClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.client = httpx.Client(base_url=f"{base_url}/rest/api/2/",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(8, connect=5), follow_redirects=False, trust_env=False, transport=transport)
        self.deadline = time.monotonic() + 30

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        if time.monotonic() >= self.deadline:
            raise HTTPException(504, "Jira request timed out. Try a smaller ticket limit")
        try:
            with self.client.stream("GET", path, params=params) as response:
                if response.status_code in {401, 403}:
                    raise HTTPException(502, "Jira rejected access. Check the token and the user's Jira permissions")
                if response.is_redirect:
                    raise HTTPException(502, "Jira redirected the request. Use its final base URL; check SSO or proxy settings")
                if response.status_code == 400:
                    raise HTTPException(400, "Jira rejected the query. Check the JQL syntax and field values")
                if response.status_code == 429:
                    raise HTTPException(503, "Jira is rate limiting requests. Try again later")
                if response.status_code != 200:
                    raise HTTPException(502, "Jira could not complete the request. Check the base URL and Jira availability")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() >= self.deadline:
                        raise HTTPException(504, "Jira request timed out. Try a smaller ticket limit")
                    body.extend(chunk)
                    if len(body) > 4 * 1024 * 1024:
                        raise HTTPException(502, "Jira response is too large. Try a smaller ticket limit")
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("Expected an object")
                return result
        except httpx.TimeoutException:
            raise HTTPException(504, "Jira request timed out. Check network access from the admin container") from None
        except httpx.HTTPError:
            raise HTTPException(502, "Could not reach Jira. Check the URL, network access, and TLS certificate") from None
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(502, "Jira returned an invalid response. Check that the URL points to its REST API") from None

    def current_user(self, username: str) -> dict:
        user = self.get("myself")
        name = user.get("name")
        if not isinstance(name, str) or user.get("active") is not True:
            raise HTTPException(502, "Jira did not return an active authenticated user")
        if name.casefold() != username.casefold():
            raise HTTPException(502, "The token belongs to a different Jira user. Check the saved username")
        return {"username": name, "display_name": str(user.get("displayName") or name)}

    def tickets(self, limit: int, jql: str = JQL) -> tuple[list[dict], int]:
        issues: list[dict] = []
        seen: set[str] = set()
        total = 0
        while len(issues) < limit:
            page = self.get("search", {"jql": jql, "startAt": len(issues), "maxResults": min(50, limit - len(issues)), "fields": ",".join(ISSUE_FIELDS)})
            try:
                total = page["total"]
                rows = page["issues"]
                if type(total) is not int or total < 0 or page["startAt"] != len(issues) or not isinstance(rows, list):
                    raise ValueError("Invalid page")
                if not rows and len(issues) < min(total, limit):
                    raise ValueError("Incomplete page")
                for issue in rows[:limit - len(issues)]:
                    if not isinstance(issue["id"], str) or not issue["id"] or issue["id"] in seen:
                        raise ValueError("Invalid or duplicate issue ID")
                    validate_issue(issue)
                    seen.add(issue["id"])
                    issues.append(issue)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(502, "Jira returned an invalid or changing ticket page. Retry the fetch") from None
            if len(issues) >= total:
                break
        return issues, total


    def issue_with_comments(self, remote_id: str) -> tuple[dict, list[dict]]:
        if re.fullmatch(r"[0-9]+", remote_id) is None:
            raise HTTPException(502, "Jira returned an invalid issue ID")
        issue = self.get(f"issue/{remote_id}", {"fields": ",".join(ISSUE_FIELDS)})
        comments: list[dict] = []
        seen: set[str] = set()
        expected_total: int | None = None
        content_bytes = 0
        try:
            validate_issue(issue)
            if issue["id"] != remote_id or not isinstance(issue["fields"].get("description", "") or "", str):
                raise ValueError("Invalid issue content")
            while True:
                page = self.get(f"issue/{remote_id}/comment", {"startAt": len(comments), "maxResults": 100})
                total, rows = page["total"], page["comments"]
                if (type(total) is not int or not 0 <= total <= 10000
                        or page["startAt"] != len(comments) or not isinstance(rows, list)
                        or (expected_total is not None and expected_total != total)
                        or len(comments) + len(rows) > total or (not rows and len(comments) < total)):
                    raise ValueError("Incomplete or changing comments")
                expected_total = total
                for comment in rows:
                    if not isinstance(comment["id"], str) or not comment["id"] or comment["id"] in seen:
                        raise ValueError("Invalid or repeated comment")
                    if not isinstance(comment["body"], str):
                        raise ValueError("Invalid comment body")
                    for timestamp in ["created", "updated"]:
                        if datetime.fromisoformat(comment[timestamp]).tzinfo is None:
                            raise ValueError("Invalid comment timestamp")
                    if not isinstance(comment.get("author", {}), dict):
                        raise ValueError("Invalid comment author")
                    content_bytes += len(comment["body"].encode("utf-8"))
                    if content_bytes > 8_000_000:
                        raise ValueError("Comment content exceeds export limit")
                    seen.add(comment["id"])
                    comments.append(comment)
                if len(comments) == total:
                    break
            # Do not publish a mixed snapshot when the issue changes during pagination.
            latest = self.get(f"issue/{remote_id}", {"fields": "updated"})
            if latest["id"] != remote_id or latest["fields"]["updated"] != issue["fields"]["updated"]:
                raise ValueError("Issue changed during export")
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, "Jira returned incomplete, oversized, or changing ticket content. No file was written; retry the export") from None
        comments.sort(key=lambda comment: (datetime.fromisoformat(comment["created"]), comment["id"]))
        return issue, comments


def validate_issue(issue: dict) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-[0-9]+", issue["key"]):
        raise ValueError("Invalid issue key")
    fields = issue["fields"]
    if not isinstance(fields["summary"], str) or not fields["summary"].strip():
        raise ValueError("Missing summary")
    if not isinstance(fields["project"]["key"], str) or not fields["project"]["key"].strip():
        raise ValueError("Missing project")
    for key in ["created", "updated"]:
        if fields.get(key) is not None and datetime.fromisoformat(fields[key]).tzinfo is None:
            raise ValueError("Timestamp lacks timezone")
    if fields.get("updated") is None:
        raise ValueError("Missing update timestamp")
    for key in ["status", "priority", "issuetype", "assignee", "reporter"]:
        if fields.get(key) is not None and not isinstance(fields[key], dict):
            raise ValueError("Invalid issue fields")
    if not isinstance(fields.get("labels", []), list) or not all(isinstance(label, str) for label in fields.get("labels", [])):
        raise ValueError("Invalid labels")
