"""Local Jira REST fixtures. No proxying, forwarding, or external network calls.

Besides the read API, the mock accepts comments (`POST issue/{id}/comment`, as Knowledge Agent posts answers) and has a
control page at `/` (published on 127.0.0.1 only) to edit tickets, add a person's comment, create tickets, make the
next comment posts fail, and see every comment posted through the API. State is saved to `STATE_PATH`.
"""

import html
import json
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

USERNAME = os.environ.get("JIRA_MOCK_USERNAME", "mock-admin")
TOKEN = os.environ.get("JIRA_MOCK_TOKEN", "jira-mock-token")
STATE_PATH = os.environ.get("JIRA_MOCK_STATE", "/data/state.json")
TITLES = ["Investigate payment API latency", "Review worker retry behavior", "Update deployment runbook",
          "Check database connection pool", "Improve request tracing", "Document service dependencies"]
STATUSES = ["Open", "In Progress", "Done"]
PERSON = {"name": "jira-user", "displayName": "Jira User"}
USERS = {key: {"key": key, "name": key, "displayName": display, "emailAddress": f"{key}@example.com", "active": True}
         for key, display in [(USERNAME, "Mock Administrator"), ("ops-bot", "Ops Bot"), ("docs-bot", "Docs Bot"),
                              ("ana", "Ana Petrović"), ("marko", "Marko Ilić")]}
# Tickets whose number is divisible by 4 are mostly unassigned; these few go to the bots and the people.
OTHER_ASSIGNEES = {4: "ops-bot", 8: "docs-bot", 12: "ana", 16: "marko"}
FAILURE_MODES = {"normal", "error", "refuse", "lost"}
MAX_REQUEST = 1024 * 1024


def new_issue(number: int, summary: str, description: str, status: int, updated: str, created: str) -> dict:
    return {
        "id": str(10000 + number), "key": f"DEMO-{number}",
        "fields": {"summary": summary, "description": description,
            "project": {"key": "DEMO"}, "issuetype": {"name": "Task"},
            "status": {"id": str(status + 1), "name": STATUSES[status]},
            "priority": {"name": ["High", "Medium", "Low"][status]},
            "assignee": USERS.get(OTHER_ASSIGNEES.get(number, "" if number % 4 == 0 else USERNAME)),
            "reporter": {"key": "mock-admin"}, "labels": ["local-mock", "demo"],
            "created": created, "updated": updated}}


def seed_issues() -> list[dict]:
    return [new_issue(
        number, f"{TITLES[(number - 1) % len(TITLES)]} ({number})",
        "h2. Investigation\n\nSynthetic local Jira ticket for *isolated integration testing*.\n\n{code:bash}\ncurl --fail http://payments/health\n{code}",
        number % 3, (datetime(2026, 9, 9, 0, 0, tzinfo=UTC) - timedelta(minutes=number)).isoformat(),
        "2026-09-01T08:00:00.000+0000",
    ) for number in range(1, 76)]


def seed_comments(issues: list[dict]) -> dict[str, list[dict]]:
    return {issue["id"]: [{
        "id": f"{issue['id']}{number}",
        "author": {"name": USERNAME, "displayName": "Mock Administrator"},
        "body": f"h3. Investigation note {number}\n\nComment {number} for {issue['key']}. " +
                ("Final pagination evidence: *retry jitter resolved the payment timeout*." if number == 5 else "Review the service logs and deployment history."),
        "created": f"2026-09-02T0{number}:00:00.000+0000", "updated": f"2026-09-02T0{number}:00:00.000+0000",
    } for number in range(1, 6)] for issue in issues}


# The state is changed in place (filter_issues and the tests read these names) and only under LOCK.
ISSUES = seed_issues()
COMMENTS = seed_comments(ISSUES)
POSTED: list[str] = []  # ids of comments posted through the API (by Knowledge Agent)
FAILURE = {"mode": "normal", "remaining": 0}
NEXT_COMMENT = [900001]
LOCK = threading.RLock()


def reset() -> None:
    ISSUES[:] = seed_issues()
    COMMENTS.clear()
    COMMENTS.update(seed_comments(ISSUES))
    POSTED.clear()
    FAILURE.update(mode="normal", remaining=0)
    NEXT_COMMENT[0] = 900001


def load() -> None:
    """Replace the state with the saved one; keep the seed when nothing was saved yet."""
    try:
        with open(STATE_PATH, encoding="utf-8") as file:
            state = json.load(file)
    except FileNotFoundError:
        return
    ISSUES[:] = state["issues"]
    COMMENTS.clear()
    COMMENTS.update(state["comments"])
    POSTED[:] = state["posted"]
    FAILURE.update(state["failure"])
    NEXT_COMMENT[0] = state["next_comment"]


def save() -> None:
    state = {"issues": ISSUES, "comments": COMMENTS, "posted": POSTED, "failure": FAILURE,
             "next_comment": NEXT_COMMENT[0]}
    temporary = f"{STATE_PATH}.tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file)
    os.replace(temporary, STATE_PATH)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def find_issue(reference: str) -> dict | None:
    return next((row for row in ISSUES if reference in {row["id"], row["key"]}), None)


def add_comment(issue: dict, body: str, author: dict) -> dict:
    """Append a comment; like Jira, the ticket's updated time becomes the comment's creation time."""
    created = now()
    comment = {"id": str(NEXT_COMMENT[0]), "author": author, "body": body, "created": created, "updated": created}
    NEXT_COMMENT[0] += 1
    COMMENTS[issue["id"]].append(comment)
    issue["fields"]["updated"] = created
    return comment


def filter_issues(jql: str) -> list[dict]:
    # A deliberately small JQL subset; unsupported expressions fail explicitly.
    order = re.compile(r"\bORDER\s+BY\s+updated\s+DESC(?:\s*,\s*key\s+DESC)?\s*$", re.I)
    sorted_by_updated = order.search(jql) is not None
    expression = order.sub("", jql).strip()
    clauses: list[tuple[str, str, str]] = []
    empty = re.compile(r"assignee\s+is\s+EMPTY", re.I)
    atom = re.compile(r'(project|status|assignee|text|key|labels)\s*(=|~)\s*("(?:[^"\\]|\\.)*"|[A-Za-z0-9_-]+)', re.I)
    while expression:
        match = empty.match(expression)
        if match:
            clauses.append(("assignee", "=", ""))
        elif match := atom.match(expression):
            field, operator, raw = match.groups()
            value = json.loads(raw) if raw.startswith('"') else raw
            if operator == "~" and field.lower() != "text":
                raise ValueError("Mock only supports ~ for text")
            clauses.append((field.lower(), operator, value.casefold()))
        else:
            raise ValueError("Unsupported mock JQL")
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
    if sorted_by_updated:
        # An edited or commented ticket moves to the top, as in Jira.
        result.sort(key=lambda issue: (datetime.fromisoformat(issue["fields"]["updated"]),
                                       int(issue["key"].split("-")[1])), reverse=True)
    return result


STYLE = """body{font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;padding:0 16px;color:#1d2125}
h1{font-size:20px}h2{font-size:16px;margin-top:28px}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #dfe1e6;padding:4px 8px;text-align:left;vertical-align:top}
pre{white-space:pre-wrap;background:#f4f5f7;padding:8px;margin:4px 0;border-radius:4px}
.agent{border-left:4px solid #0c66e4;padding-left:10px;margin:12px 0}.person{border-left:4px solid #dfe1e6;padding-left:10px;margin:12px 0}
.muted{color:#626f86}form.inline{display:inline}input[type=text],textarea{width:100%;box-sizing:border-box}
.box{background:#f7f8f9;padding:12px;border-radius:4px;margin:8px 0}"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)}</title>"
            f"<style>{STYLE}</style></head><body>{body}</body></html>")


def comment_html(issue: dict, comment: dict, show_issue: bool = False) -> str:
    agent = comment["id"] in POSTED
    who = "Knowledge Agent (posted through the API)" if agent else comment["author"].get("displayName", "")
    where = f'<a href="/mock/{quote(issue["key"])}">{html.escape(issue["key"])}</a> · ' if show_issue else ""
    return (f'<div class="{"agent" if agent else "person"}"><div class="muted">{where}{html.escape(who)} · '
            f'{html.escape(comment["created"])} · id {html.escape(comment["id"])}</div>'
            f'<pre>{html.escape(comment["body"])}</pre></div>')


def agent_comments() -> list[tuple[dict, dict]]:
    rows = [(issue, comment) for issue in ISSUES for comment in COMMENTS[issue["id"]] if comment["id"] in POSTED]
    return sorted(rows, key=lambda row: POSTED.index(row[1]["id"]), reverse=True)


def assignee_options(current: str = "") -> str:
    return '<option value="">Unassigned</option>' + "".join(
        f'<option value="{user["key"]}"{" selected" if user["key"] == current else ""}>{html.escape(user["displayName"])}</option>'
        for user in USERS.values())


def home_html() -> str:
    posted = "".join(comment_html(issue, comment, True) for issue, comment in agent_comments())
    rows = "".join(
        f'<tr><td><a href="/mock/{quote(issue["key"])}">{html.escape(issue["key"])}</a></td>'
        f'<td>{html.escape(issue["fields"]["summary"])}</td><td>{html.escape(issue["fields"]["status"]["name"])}</td>'
        f'<td>{html.escape((issue["fields"]["assignee"] or {}).get("displayName", ""))}</td>'
        f'<td>{html.escape(issue["fields"]["updated"])}</td><td>{len(COMMENTS[issue["id"]])}</td>'
        f'<td>{sum(comment["id"] in POSTED for comment in COMMENTS[issue["id"]]) or ""}</td></tr>'
        for issue in filter_issues("ORDER BY updated DESC"))
    modes = {"normal": "Normal: accept comments", "error": "Fail with 500 (the worker retries)",
             "refuse": "Refuse with 403 (fails at once)", "lost": "Save the comment but answer 500 (lost response)"}
    options = "".join(f'<option value="{mode}"{" selected" if FAILURE["mode"] == mode else ""}>{label}</option>'
                      for mode, label in modes.items())
    state = ("Comment posts are accepted." if FAILURE["mode"] == "normal"
             else f'Next {FAILURE["remaining"]} comment post(s): {modes[FAILURE["mode"]]}.')
    return page("Jira mock", f"""<h1>Jira mock</h1>
<p class="muted">A local test fixture, not Jira. Edit a ticket or add a comment as a person, then run the Jira pull in
Knowledge Agent: the change is recorded as <code>jira.ticket.updated</code> (a new ticket as
<code>jira.ticket.created</code>).</p>
<h2>Agent comments ({len(POSTED)})</h2>{posted or '<p class="muted">Nothing posted yet.</p>'}
<h2>Comment posting</h2><div class="box"><p>{html.escape(state)}</p>
<form method=post action="/mock/failure"><select name=mode>{options}</select>
for the next <input name=count type=number min=1 max=10 value=3 style="width:4em"> post(s)
<button>Apply</button></form></div>
<h2>New ticket</h2><div class="box"><form method=post action="/mock/tickets">
<p><input type=text name=summary placeholder="Summary" required maxlength=255></p>
<p><textarea name=description rows=3 placeholder="Description (Jira wiki markup)"></textarea></p>
<p>Assignee <select name=assignee>{assignee_options()}</select></p>
<button>Create</button></form></div>
<h2>Tickets ({len(ISSUES)})</h2><table><tr><th>Key</th><th>Summary</th><th>Status</th><th>Assignee</th><th>Updated</th>
<th>Comments</th><th>Agent</th></tr>{rows}</table>
<h2>Reset</h2><form method=post action="/mock/reset"><button>Reset to the 75 original tickets</button>
<span class="muted"> Removes every edit, new ticket and comment.</span></form>""")


def issue_html(issue: dict) -> str:
    fields = issue["fields"]
    options = "".join(f'<option{" selected" if fields["status"]["name"] == status else ""}>{status}</option>'
                      for status in STATUSES)
    comments = "".join(comment_html(issue, comment) for comment in COMMENTS[issue["id"]])
    key = quote(issue["key"])
    current = (fields["assignee"] or {}).get("key", "")
    assignees = assignee_options(current)
    authors = '<option value="">Jira User</option>' + "".join(
        f'<option value="{user["key"]}">{html.escape(user["displayName"])}</option>' for user in USERS.values())
    return page(issue["key"], f"""<p><a href="/">← Jira mock</a></p>
<h1>{html.escape(issue["key"])} · {html.escape(fields["summary"])}</h1>
<p class="muted">id {html.escape(issue["id"])} · {html.escape(fields["status"]["name"])} · updated
{html.escape(fields["updated"])}</p><pre>{html.escape(fields["description"])}</pre>
<h2>Comments ({len(COMMENTS[issue["id"]])})</h2>{comments}
<h2>Add a comment as a person</h2><div class="box"><form method=post action="/mock/{key}/comment">
<p><textarea name=body rows=4 required></textarea></p><p>Comment as <select name=author>{authors}</select></p><button>Add comment</button></form></div>
<h2>Edit</h2><div class="box"><form method=post action="/mock/{key}/edit">
<p><input type=text name=summary value="{html.escape(fields["summary"])}" required maxlength=255></p>
<p><textarea name=description rows=6>{html.escape(fields["description"])}</textarea></p>
<p><select name=status>{options}</select> Assignee <select name=assignee>{assignees}</select></p><button>Save</button></form></div>""")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: object) -> None:
        # Keep Authorization headers and user-supplied request paths out of logs.
        pass

    def respond(self, status: int, payload: dict | list) -> None:
        self.send(status, json.dumps(payload).encode(), "application/json")

    def send(self, status: int, body: bytes, content_type: str, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        """Answer the token failures of the Jira API; True when the token is the mock's."""
        authorization = self.headers.get("Authorization", "")
        if authorization == "Bearer mock-forbidden":
            self.respond(403, {"errorMessages": ["Synthetic permission failure"]})
        elif authorization == "Bearer mock-rate-limited":
            self.respond(429, {"errorMessages": ["Synthetic rate limit"]})
        elif authorization != f"Bearer {TOKEN}":
            self.respond(401, {"errorMessages": ["Invalid mock token"]})
        else:
            return True
        return False

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        if url.path == "/health":
            self.respond(200, {"status": "ok", "mock": True})
            return
        with LOCK:
            if url.path == "/":
                self.send(200, home_html().encode(), "text/html; charset=utf-8")
            elif url.path == "/mock/comments":
                self.respond(200, [{"issue_id": issue["id"], "issue_key": issue["key"], **comment}
                                   for issue, comment in agent_comments()])
            elif url.path == "/mock/state":
                self.respond(200, {"issues": ISSUES, "comments": COMMENTS, "posted": POSTED, "failure": FAILURE})
            elif (match := re.fullmatch(r"/mock/([A-Z][A-Z0-9]*-[0-9]+)", url.path)) and find_issue(match[1]):
                self.send(200, issue_html(find_issue(match[1])).encode(), "text/html; charset=utf-8")
            elif url.path.startswith("/jira/") and self.authorized():
                self.api_get(url)
            elif not url.path.startswith("/jira/"):
                self.respond(404, {"errorMessages": ["Unknown mock page"]})

    def api_get(self, url) -> None:
        if url.path == "/jira/rest/api/2/myself":
            self.respond(200, USERS[USERNAME])
        elif url.path == "/jira/rest/api/2/user":
            user = USERS.get(parse_qs(url.query).get("key", [""])[0])
            if user:
                self.respond(200, user)
            else:
                self.respond(404, {"errorMessages": ["The specified user does not exist"]})
        elif url.path == "/jira/rest/api/2/user/search":
            text = parse_qs(url.query).get("username", [""])[0].casefold()
            self.respond(200, [user for user in USERS.values()
                               if text in f"{user['key']} {user['name']} {user['displayName']}".casefold()][:50])
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
            page = issues[start:start + size]
            if "comment" in query.get("fields", [""])[0].split(","):
                # Like Jira, the search result then carries each ticket's comments.
                page = [{**issue, "fields": {**issue["fields"], "comment": {
                    "startAt": 0, "maxResults": len(COMMENTS[issue["id"]]), "total": len(COMMENTS[issue["id"]]),
                    "comments": COMMENTS[issue["id"]]}}} for issue in page]
            self.respond(200, {"startAt": start, "maxResults": size, "total": len(issues), "issues": page})
        elif match := re.fullmatch(r"/jira/rest/api/2/issue/([A-Za-z0-9_-]+)(/comment)?", url.path):
            issue = find_issue(match[1])
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

    def read_body(self) -> bytes | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST:
            self.respond(413, {"errorMessages": ["Request too large for the mock"]})
            return None
        return self.rfile.read(length)

    def do_POST(self) -> None:
        url = urlsplit(self.path)
        body = self.read_body()
        if body is None:
            return
        with LOCK:
            if match := re.fullmatch(r"/jira/rest/api/2/issue/([A-Za-z0-9_-]+)/comment", url.path):
                if self.authorized():
                    self.api_comment(match[1], body)
            elif url.path.startswith("/mock/"):
                self.control(url.path, {key: values[0] for key, values in parse_qs(body.decode(errors="replace")).items()})
            else:
                self.respond(404, {"errorMessages": ["This is a local API mock, not a Jira installation"]})

    def do_PUT(self) -> None:
        url = urlsplit(self.path)
        body = self.read_body()
        if body is None:
            return
        with LOCK:
            if match := re.fullmatch(r"/jira/rest/api/2/issue/([A-Za-z0-9_-]+)/assignee", url.path):
                if self.authorized():
                    self.api_assign(match[1], body)
            else:
                self.respond(404, {"errorMessages": ["This is a local API mock, not a Jira installation"]})

    def api_assign(self, reference: str, raw: bytes) -> None:
        """Like Jira Data Center: {"name": login} assigns, {"name": null} unassigns; 204 and the ticket is updated."""
        issue = find_issue(reference)
        if issue is None:
            self.respond(404, {"errorMessages": ["Unknown mock issue"]})
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or "name" not in payload or payload["name"] is not None and (
                not isinstance(payload["name"], str) or payload["name"] not in USERS):
            self.respond(400, {"errorMessages": ["The assignee name must be an existing user name or null"]})
            return
        issue["fields"].update(assignee=USERS.get(payload["name"]), updated=now())
        save()
        self.send(204, b"", "application/json")

    def api_comment(self, reference: str, raw: bytes) -> None:
        issue = find_issue(reference)
        if issue is None:
            self.respond(404, {"errorMessages": ["Unknown mock issue"]})
            return
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("body"), str) or not payload["body"].strip():
            self.respond(400, {"errorMessages": ["Comment body is required"]})
            return
        mode = FAILURE["mode"]
        if mode != "normal":
            FAILURE["remaining"] -= 1
            if FAILURE["remaining"] <= 0:
                FAILURE.update(mode="normal", remaining=0)
        if mode == "refuse":
            save()
            self.respond(403, {"errorMessages": ["Synthetic refusal: no Add Comments permission"]})
            return
        if mode == "error":
            save()
            self.respond(500, {"errorMessages": ["Synthetic server error"]})
            return
        comment = add_comment(issue, payload["body"], USERS[USERNAME])
        POSTED.append(comment["id"])
        save()
        if mode == "lost":
            self.respond(500, {"errorMessages": ["Synthetic error after the comment was saved"]})
            return
        self.respond(201, {"self": f"/jira/rest/api/2/issue/{issue['id']}/comment/{comment['id']}", **comment})

    def control(self, path: str, form: dict[str, str]) -> None:
        """The control page's forms; each change is saved and answered with a redirect to the page it came from."""
        target = "/"
        if path == "/mock/reset":
            reset()
        elif path == "/mock/failure":
            mode, count = form.get("mode", ""), form.get("count", "1")
            if mode not in FAILURE_MODES or not count.isdigit() or not 1 <= int(count) <= 10:
                self.respond(400, {"errorMessages": ["mode must be normal, error, refuse or lost; count 1-10"]})
                return
            FAILURE.update(mode=mode, remaining=0 if mode == "normal" else int(count))
        elif path == "/mock/tickets":
            summary, assignee = form.get("summary", "").strip(), form.get("assignee", "")
            if not 1 <= len(summary) <= 255 or assignee and assignee not in USERS:
                self.respond(400, {"errorMessages": ["A summary of 1-255 characters and a known assignee are required"]})
                return
            number = max(int(issue["key"].split("-")[1]) for issue in ISSUES) + 1
            created = now()
            issue = new_issue(number, summary, form.get("description", ""), 0, created, created)
            issue["fields"]["assignee"] = USERS.get(assignee)
            ISSUES.append(issue)
            COMMENTS[issue["id"]] = []
            target = f"/mock/{issue['key']}"
        elif match := re.fullmatch(r"/mock/([A-Z][A-Z0-9]*-[0-9]+)/(edit|comment)", path):
            issue = find_issue(match[1])
            if issue is None:
                self.respond(404, {"errorMessages": ["Unknown mock issue"]})
                return
            target = f"/mock/{issue['key']}"
            if match[2] == "comment":
                if not form.get("body", "").strip():
                    self.respond(400, {"errorMessages": ["Comment body is required"]})
                    return
                author = PERSON if not form.get("author") else USERS.get(form["author"])
                if author is None:
                    self.respond(400, {"errorMessages": ["Unknown user"]})
                    return
                add_comment(issue, form["body"], author)
            else:
                summary, status = form.get("summary", "").strip(), form.get("status", "")
                assignee = form.get("assignee", "")
                if not 1 <= len(summary) <= 255 or status not in STATUSES or assignee and assignee not in USERS:
                    self.respond(400, {"errorMessages": ["A summary of 1-255 characters, a known status and a known assignee are required"]})
                    return
                fields = issue["fields"]
                fields.update(assignee=USERS.get(assignee), summary=summary, description=form.get("description", ""), updated=now(),
                              status={"id": str(STATUSES.index(status) + 1), "name": status})
        else:
            self.respond(404, {"errorMessages": ["Unknown mock page"]})
            return
        save()
        self.send(303, b"", "text/plain", target)


if __name__ == "__main__":
    load()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
