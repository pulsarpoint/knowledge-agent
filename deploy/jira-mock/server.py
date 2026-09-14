"""Local Jira REST fixtures. No proxying, forwarding, or external network calls."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

USERNAME = os.environ.get("JIRA_MOCK_USERNAME", "mock-admin")
TOKEN = os.environ.get("JIRA_MOCK_TOKEN", "jira-mock-token")
TITLES = ["Investigate payment API latency", "Review worker retry behavior", "Update deployment runbook",
          "Check database connection pool", "Improve request tracing", "Document service dependencies"]
ISSUES = [{
    "id": str(10000 + number), "key": f"DEMO-{number}",
    "fields": {"summary": f"{TITLES[(number - 1) % len(TITLES)]} ({number})",
        "description": "h2. Investigation\n\nSynthetic local Jira ticket for *isolated integration testing*.\n\n{code:bash}\ncurl --fail http://payments/health\n{code}",
        "project": {"key": "DEMO"}, "issuetype": {"name": "Task"},
        "status": {"id": str(number % 3 + 1), "name": ["Open", "In Progress", "Done"][number % 3]},
        "priority": {"name": ["High", "Medium", "Low"][number % 3]},
        "assignee": None if number % 4 == 0 else {"key": "mock-admin", "displayName": "Mock Administrator"},
        "reporter": {"key": "mock-admin"}, "labels": ["local-mock", "demo"],
        "created": "2026-09-01T08:00:00.000+0000",
        "updated": (datetime(2026, 9, 9, 0, 0, tzinfo=UTC) - timedelta(minutes=number)).isoformat()}
} for number in range(1, 76)]


COMMENTS = {issue["id"]: [{
    "id": f"{issue['id']}{number}",
    "author": {"name": USERNAME, "displayName": "Mock Administrator"},
    "body": f"h3. Investigation note {number}\n\nComment {number} for {issue['key']}. " +
            ("Final pagination evidence: *retry jitter resolved the payment timeout*." if number == 5 else "Review the service logs and deployment history."),
    "created": f"2026-09-02T0{number}:00:00.000+0000", "updated": f"2026-09-02T0{number}:00:00.000+0000",
} for number in range(1, 6)] for issue in ISSUES}


def filter_issues(jql: str) -> list[dict]:
    # A deliberately small JQL subset; unsupported expressions fail explicitly.
    expression = re.sub(r"\bORDER\s+BY\s+updated\s+DESC(?:\s*,\s*key\s+DESC)?\s*$", "", jql, flags=re.I).strip()
    clauses: list[tuple[str, str, str]] = []
    atom = re.compile(r'(project|status|assignee|text|key|labels)\s*(=|~)\s*("(?:[^"\\]|\\.)*"|[A-Za-z0-9_-]+)', re.I)
    while expression:
        match = atom.match(expression)
        if match is None:
            raise ValueError("Unsupported mock JQL")
        field, operator, raw = match.groups()
        value = json.loads(raw) if raw.startswith('"') else raw
        if operator == "~" and field.lower() != "text":
            raise ValueError("Mock only supports ~ for text")
        clauses.append((field.lower(), operator, value.casefold()))
        expression = expression[match.end():].strip()
        if expression:
            separator = re.match(r"AND\s+", expression, re.I)
            if separator is None:
                raise ValueError("Unsupported mock JQL")
            expression = expression[separator.end():]
            if not expression:
                raise ValueError("Incomplete mock JQL")
    result = []
    for issue in ISSUES:
        fields = issue["fields"]
        values = {"project": [fields["project"]["key"]], "status": [fields["status"]["name"]],
                  "assignee": [(fields["assignee"] or {}).get("key", "")], "key": [issue["key"]],
                  "labels": fields["labels"], "text": [fields["summary"] + " " + fields["description"] + " " +
                    " ".join(comment["body"] for comment in COMMENTS[issue["id"]])]}
        if all(any(value in candidate.casefold() if operator == "~" else value == candidate.casefold()
                   for candidate in values[field]) for field, operator, value in clauses):
            result.append(issue)
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        # Keep Authorization headers and user-supplied request paths out of logs.
        pass

    def respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/health":
            self.respond(200, {"status": "ok", "mock": True})
            return
        authorization = self.headers.get("Authorization", "")
        if authorization == "Bearer mock-forbidden":
            self.respond(403, {"errorMessages": ["Synthetic permission failure"]})
            return
        if authorization == "Bearer mock-rate-limited":
            self.respond(429, {"errorMessages": ["Synthetic rate limit"]})
            return
        if authorization != f"Bearer {TOKEN}":
            self.respond(401, {"errorMessages": ["Invalid mock token"]})
            return
        if url.path == "/jira/rest/api/2/myself":
            self.respond(200, {"name": USERNAME, "key": USERNAME, "displayName": "Mock Administrator", "active": True})
        elif url.path == "/jira/rest/api/2/search":
            query = parse_qs(url.query)
            try:
                issues = filter_issues(query.get("jql", [""])[0])
                start = max(0, int(query.get("startAt", ["0"])[0]))
                # Force pagination even for the default UI limit.
                size = min(10, max(1, int(query.get("maxResults", ["25"])[0])))
            except ValueError:
                self.respond(400, {"errorMessages": ["Invalid pagination or unsupported mock JQL"]})
                return
            self.respond(200, {"startAt": start, "maxResults": size, "total": len(issues), "issues": issues[start:start + size]})
        elif match := re.fullmatch(r"/jira/rest/api/2/issue/([A-Za-z0-9_-]+)(/comment)?", url.path):
            issue = next((row for row in ISSUES if match[1] in {row["id"], row["key"]}), None)
            if issue is None:
                self.respond(404, {"errorMessages": ["Unknown mock issue"]})
                return
            if match[2] is None:
                self.respond(200, issue)
                return
            query = parse_qs(url.query)
            try:
                start = max(0, int(query.get("startAt", ["0"])[0]))
                size = min(2, max(1, int(query.get("maxResults", ["100"])[0])))
            except ValueError:
                self.respond(400, {"errorMessages": ["Invalid pagination"]})
                return
            comments = COMMENTS[issue["id"]]
            self.respond(200, {"startAt": start, "maxResults": size, "total": len(comments), "comments": comments[start:start + size]})
        else:
            self.respond(404, {"errorMessages": ["This is a local API mock, not a Jira installation"]})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
