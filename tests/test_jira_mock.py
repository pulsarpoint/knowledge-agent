import json
import os
import runpy
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

SERVER = Path(__file__).resolve().parents[1] / "deploy" / "jira-mock" / "server.py"
API = "/jira/rest/api/2"
TOKEN = "Bearer jira-mock-token"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object) -> None:
        return None


OPENER = urllib.request.build_opener(NoRedirect)


class JiraMockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.start()

    def tearDown(self) -> None:
        self.stop()
        self.folder.cleanup()

    def start(self) -> None:
        # A fresh module namespace each time: a restart reads only what was saved.
        # run_path returns a copy of the module globals, so the state file is set through the environment.
        with unittest.mock.patch.dict(os.environ, {"JIRA_MOCK_STATE": str(Path(self.folder.name) / "state.json")}):
            self.mock = runpy.run_path(str(SERVER))
        self.mock["load"]()
        self.server = self.mock["ThreadingHTTPServer"](("127.0.0.1", 0), self.mock["Handler"])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def call(self, method: str, path: str, body: dict | None = None, *, form: bool = False,
             token: str | None = TOKEN) -> tuple[int, dict | str, dict]:
        data, headers = None, {}
        if token:
            headers["Authorization"] = token
        if body is not None:
            data = urlencode(body).encode() if form else json.dumps(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with OPENER.open(request) as response:
                status, raw, response_headers = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as error:
            with error:
                status, raw, response_headers = error.code, error.read(), dict(error.headers)
        text = raw.decode()
        is_json = response_headers.get("Content-Type", "").startswith("application/json")
        return status, json.loads(text) if is_json else text, response_headers

    def issue(self, key: str) -> dict:
        return self.call("GET", f"{API}/issue/{key}")[1]

    def all_comments(self, issue_id: str) -> list[dict]:
        comments: list[dict] = []
        while True:
            page = self.call("GET", f"{API}/issue/{issue_id}/comment?startAt={len(comments)}&maxResults=100")[1]
            comments += page["comments"]
            if len(comments) >= page["total"]:
                return comments

    def test_agent_comment_is_stored_moves_updated_and_pages_with_the_others(self) -> None:
        status, comment, _ = self.call("POST", f"{API}/issue/10003/comment", {"body": "The answer.\n\n— Bot"})
        self.assertEqual(status, 201)
        self.assertEqual(comment["body"], "The answer.\n\n— Bot")
        self.assertIsNotNone(datetime.fromisoformat(comment["created"]).tzinfo)
        self.assertEqual(self.issue("DEMO-3")["fields"]["updated"], comment["created"])
        self.assertEqual([row["id"] for row in self.all_comments("10003")][-1], comment["id"])
        listed = self.call("GET", "/mock/comments")[1]
        self.assertEqual([(row["issue_key"], row["id"], row["body"]) for row in listed],
                         [("DEMO-3", comment["id"], "The answer.\n\n— Bot")])
        self.assertIn("The answer.", self.call("GET", "/")[1])
        # The edited ticket comes first when sorted by updated, as in Jira.
        search = self.call("GET", f"{API}/search?jql=project%20%3D%20DEMO%20ORDER%20BY%20updated%20DESC")[1]
        self.assertEqual(search["issues"][0]["key"], "DEMO-3")

    def test_comment_needs_the_token_a_body_and_a_known_issue(self) -> None:
        self.assertEqual(self.call("POST", f"{API}/issue/10003/comment", {"body": "x"}, token=None)[0], 401)
        self.assertEqual(self.call("POST", f"{API}/issue/10003/comment", {"body": "x"},
                                   token="Bearer mock-forbidden")[0], 403)
        self.assertEqual(self.call("POST", f"{API}/issue/10003/comment", {"body": " "})[0], 400)
        self.assertEqual(self.call("POST", f"{API}/issue/99999/comment", {"body": "x"})[0], 404)
        self.assertEqual(self.call("GET", "/mock/comments")[1], [])

    def test_failure_switch_applies_to_the_next_posts_then_returns_to_normal(self) -> None:
        self.assertEqual(self.call("POST", "/mock/failure", {"mode": "error", "count": "2"}, form=True)[0], 303)
        self.assertEqual(self.call("POST", f"{API}/issue/10001/comment", {"body": "a"})[0], 500)
        self.assertEqual(self.call("POST", f"{API}/issue/10001/comment", {"body": "a"})[0], 500)
        self.assertEqual(self.call("POST", f"{API}/issue/10001/comment", {"body": "a"})[0], 201)
        self.call("POST", "/mock/failure", {"mode": "refuse", "count": "1"}, form=True)
        self.assertEqual(self.call("POST", f"{API}/issue/10001/comment", {"body": "b"})[0], 403)
        self.call("POST", "/mock/failure", {"mode": "lost", "count": "1"}, form=True)
        self.assertEqual(self.call("POST", f"{API}/issue/10001/comment", {"body": "c"})[0], 500)
        # The lost response's comment is on the ticket: a retry must find it instead of posting again.
        self.assertEqual([row["body"] for row in self.call("GET", "/mock/comments")[1]], ["c", "a"])
        self.assertEqual(self.call("POST", "/mock/failure", {"mode": "nope", "count": "1"}, form=True)[0], 400)

    def test_page_edits_person_comments_and_new_tickets_move_updated(self) -> None:
        before = self.issue("DEMO-5")["fields"]["updated"]
        status, _, headers = self.call("POST", "/mock/DEMO-5/edit",
                                       {"summary": "Renamed", "description": "New text", "status": "Done"}, form=True)
        self.assertEqual((status, headers["Location"]), (303, "/mock/DEMO-5"))
        fields = self.issue("DEMO-5")["fields"]
        self.assertEqual((fields["summary"], fields["description"], fields["status"]), ("Renamed", "New text", {"id": "3", "name": "Done"}))
        self.assertGreater(datetime.fromisoformat(fields["updated"]), datetime.fromisoformat(before))
        self.assertEqual(self.call("POST", "/mock/DEMO-5/comment", {"body": "From a person"}, form=True)[0], 303)
        last = self.all_comments("10005")[-1]
        self.assertEqual((last["body"], last["author"]["name"]), ("From a person", "jira-user"))
        self.assertEqual(self.issue("DEMO-5")["fields"]["updated"], last["created"])
        self.assertEqual(self.call("GET", "/mock/comments")[1], [])  # a person's comment is not the agent's
        self.assertIn("From a person", self.call("GET", "/mock/DEMO-5")[1])
        status, _, headers = self.call("POST", "/mock/tickets", {"summary": "Brand new", "description": "d"}, form=True)
        self.assertEqual((status, headers["Location"]), (303, "/mock/DEMO-76"))
        self.assertEqual(self.issue("DEMO-76")["fields"]["summary"], "Brand new")
        self.assertEqual(self.all_comments("10076"), [])
        self.assertEqual(self.call("POST", "/mock/DEMO-5/edit", {"summary": "", "status": "Done"}, form=True)[0], 400)
        self.assertEqual(self.call("POST", "/mock/DEMO-999/comment", {"body": "x"}, form=True)[0], 404)

    def test_state_survives_a_restart_and_reset_restores_the_seed(self) -> None:
        comment = self.call("POST", f"{API}/issue/10002/comment", {"body": "Kept"})[1]
        self.call("POST", "/mock/tickets", {"summary": "Kept ticket"}, form=True)
        self.stop()
        self.start()
        self.assertEqual([row["id"] for row in self.call("GET", "/mock/comments")[1]], [comment["id"]])
        self.assertEqual(self.issue("DEMO-76")["fields"]["summary"], "Kept ticket")
        self.assertEqual(self.call("POST", "/mock/reset", {}, form=True)[0], 303)
        self.assertEqual(self.call("GET", "/mock/comments")[1], [])
        self.assertEqual(self.call("GET", f"{API}/issue/DEMO-76")[0], 404)
        self.assertEqual(len(self.all_comments("10002")), 5)
        self.stop()
        self.start()
        self.assertEqual(self.call("GET", f"{API}/issue/DEMO-76")[0], 404)

    def test_control_page_and_unknown_paths(self) -> None:
        self.assertIn("Jira mock", self.call("GET", "/", token=None)[1])
        self.assertEqual(self.call("GET", "/mock/DEMO-999", token=None)[0], 404)
        self.assertEqual(self.call("GET", f"{API}/myself", token=None)[0], 401)
        self.assertEqual(self.call("POST", "/elsewhere", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
