import copy
import hashlib
import json
import runpy
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from test_api import _client
from test_jira import SETTINGS, jira_response
from test_repositories import HEADERS

MOCK = runpy.run_path(str(Path(__file__).resolve().parents[3] / "deploy/jira-mock/server.py"))


@pytest.fixture
def exporting(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_BACKOFFICE_KEY", "rag-export-key")
    state = {"issues": copy.deepcopy(MOCK["ISSUES"][:3]), "comments": copy.deepcopy(MOCK["COMMENTS"]),
             "files": {}, "jira_collection": None, "rag_error": None, "jira_calls": [], "rag_calls": [], "fail_page": None, "after_comments": None}

    def jira(request):
        state["jira_calls"].append(request)
        assert request.headers["Authorization"] == "Bearer private-jira-token"
        if "/issue/" not in request.url.path:
            return jira_response(request)
        remote_id = request.url.path.split("/issue/")[1].split("/")[0]
        if request.url.path.endswith("/comment"):
            start = int(request.url.params["startAt"])
            if start == state["fail_page"]:
                return httpx.Response(403, text="private-jira-token")
            rows = state["comments"][remote_id]
            response = httpx.Response(200, json={"startAt": start, "total": len(rows), "comments": rows[start:start + 2]})
            if start + 2 >= len(rows) and state["after_comments"] is not None:
                state["after_comments"]()
            return response
        return httpx.Response(200, json=next(row for row in state["issues"] if row["id"] == remote_id))

    def rag(request):
        state["rag_calls"].append(request)
        assert request.headers["x-rag-key"] == "rag-export-key"
        assert request.headers["x-rag-actor"]
        with store.connect() as conn:
            assert str(store.project(conn, "shop")) in request.url.path
        if state["rag_error"] is not None:
            error = state["rag_error"]
            if isinstance(error, Exception):
                raise error
            return httpx.Response(error[0], json={"error": {"message": error[1]}})
        if request.url.path.endswith("/paths"):
            assert request.method == "PUT"
            assert json.loads(request.content) == {"source_path": "rag-data/jira", "owner_kind": "jira"}
            state["jira_collection"] = request.url.path.split("/by-name/")[1].split("/")[0]  # RAG re-points the owner
            return httpx.Response(200, json={"name": state["jira_collection"], "paths": []})
        assert request.url.path.endswith("/file")
        if request.method == "GET":
            assert request.url.params["collection"] == state["jira_collection"]
            result = state["files"].get(request.url.params["path"])
            return httpx.Response(200, json=result) if result is not None else httpx.Response(404, json={"error": {"message": "Document not found"}})
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert set(body) == {"path", "content", "expected_hash"} and body["path"].startswith("rag-data/jira/")
        previous = state["files"].get(body["path"])
        assert body["expected_hash"] == (previous["hash"] if previous is not None else None)
        result = {"path": body["path"], "content": body["content"], "hash": hashlib.sha256(body["content"].encode()).hexdigest()}
        state["files"][body["path"]] = result
        return httpx.Response(200, json={"path": result["path"], "hash": result["hash"]})

    with httpx.Client(base_url="http://rag.test", transport=httpx.MockTransport(rag)) as backend:
        client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(jira), backoffice_client=backend)
        saved = client.put("/api/jira/settings", headers=HEADERS, json=SETTINGS)
        assert saved.status_code == 200
        snapshot = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 3}).json()
        state["jira_calls"].clear()
        yield client, snapshot, state


def export(client, snapshot, **payload):
    return client.post(f"/api/jira/tickets/{snapshot['tickets'][0]['id']}/export", headers=HEADERS,
                       json={"collection": "jira", "connection_revision": snapshot["connection"]["revision"], **payload})


def test_export_full_issue_all_comment_pages_and_incremental_content(exporting, store):
    client, snapshot, state = exporting
    response = export(client, snapshot)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "created" and response.json()["comments"] == 5
    assert response.json()["collection"] == "jira" and response.json()["path"] == "rag-data/jira/DEMO-1.md"
    document = state["files"]["rag-data/jira/DEMO-1.md"]
    assert "## Investigation" in document["content"] and "```bash" in document["content"]
    assert "**retry jitter resolved the payment timeout**" in document["content"]
    assert document["content"].count("### Comment ") == 5
    assert [int(call.url.params["startAt"]) for call in state["jira_calls"] if call.url.path.endswith("/comment")] == [0, 2, 4]
    writes = sum(call.method == "PUT" and call.url.path.endswith("/file") for call in state["rag_calls"])
    unchanged = export(client, snapshot).json()
    assert unchanged["status"] == "unchanged" and unchanged["hash"] == document["hash"]
    assert sum(call.method == "PUT" and call.url.path.endswith("/file") for call in state["rag_calls"]) == writes
    state["comments"]["10001"][-1]["body"] = "Updated final comment"
    updated = export(client, snapshot).json()
    assert updated["status"] == "updated" and updated["hash"] != document["hash"]
    assert "Updated final comment" in state["files"]["rag-data/jira/DEMO-1.md"]["content"]
    # Deleting a remote comment removes it on the next export.
    state["comments"]["10001"] = []
    assert export(client, snapshot).json()["comments"] == 0
    assert "### Comment " not in state["files"]["rag-data/jira/DEMO-1.md"]["content"]
    with store.connect() as conn:
        events = conn.execute("SELECT details FROM ka.audit_events WHERE action='jira.ticket_exported'").fetchall()
    assert len(events) == 4 and events[0]["details"]["path"] == "rag-data/jira/DEMO-1.md"
    puts = [json.loads(call.content) for call in state["rag_calls"] if call.method == "PUT" and call.url.path.endswith("/file")]
    assert puts[0]["path"] == "rag-data/jira/DEMO-1.md" and "collection" not in puts[0]
    assert "private-jira-token" not in str(events) + document["content"] + response.text


@pytest.mark.parametrize("problem", ["page_failure", "duplicate", "wrong_issue", "changed_issue", "changed_settings", "changed_selection"])
def test_incomplete_or_stale_export_never_publishes(exporting, store, problem):
    client, snapshot, state = exporting
    if problem == "page_failure":
        state["fail_page"] = 4
    elif problem == "duplicate":
        state["comments"]["10001"][-1]["id"] = state["comments"]["10001"][0]["id"]
    elif problem == "wrong_issue":
        state["issues"][0]["key"] = "OTHER-1"
    elif problem == "changed_issue":
        state["after_comments"] = lambda: state["issues"][0]["fields"].update(updated="2026-09-10T00:00:00+0000")
    else:
        def changed():
            with store.connect() as conn:
                if problem == "changed_settings":
                    conn.execute("UPDATE ka.jira_connections SET revision=revision+1")
                else:
                    conn.execute("UPDATE ka.jira_connections SET fetch_revision=fetch_revision+1")
        state["after_comments"] = changed
    response = export(client, snapshot)
    assert response.status_code in {409, 502}, response.text
    assert state["rag_calls"] == [] and state["files"] == {}
    assert "private-jira-token" not in response.text


def test_failed_refresh_preserves_existing_file_and_unrelated_files_are_protected(exporting):
    client, snapshot, state = exporting
    assert export(client, snapshot).status_code == 200
    original = copy.deepcopy(state["files"])
    state["fail_page"] = 4
    assert export(client, snapshot).status_code == 502
    assert state["files"] == original
    state["fail_page"] = None
    state["files"]["rag-data/jira/DEMO-1.md"]["content"] = "User-uploaded documentation"
    assert export(client, snapshot).status_code == 409
    assert state["files"]["rag-data/jira/DEMO-1.md"]["content"] == "User-uploaded documentation"


@pytest.mark.parametrize("collection", ["../escape", "/absolute", "jira/tickets", "jira.md", "jira\\tickets", "", "Jira", "jira_tickets", "a--b", "x" * 61])
def test_export_rejects_unsafe_collection_before_any_outbound_request(exporting, collection):
    client, snapshot, state = exporting
    assert export(client, snapshot, collection=collection).status_code == 400
    assert state["jira_calls"] == [] and state["rag_calls"] == []


def test_export_requires_admin_identity_current_connection_and_saved_ticket(exporting):
    client, snapshot, state = exporting
    path = f"/api/jira/tickets/{snapshot['tickets'][0]['id']}/export"
    body = {"collection": "jira", "connection_revision": 1}
    for headers in [{}, {"x-forwarded-groups": "readers"}, {"x-forwarded-groups": "docs-admins"}]:
        assert client.post(path, headers=headers, json=body).status_code == 403
    assert export(client, snapshot, connection_revision=2).status_code == 409
    assert client.post(f"/api/jira/tickets/{uuid4()}/export", headers=HEADERS, json=body).status_code == 404
    assert state["jira_calls"] == [] and state["rag_calls"] == []


def test_custom_jql_is_forwarded_and_only_successful_query_is_saved(store, tmp_path):
    query = 'project = DEMO AND status = "Open" ORDER BY updated DESC'
    rejected = False
    def handler(request):
        if request.url.path.endswith("/myself"):
            return jira_response(request)
        if rejected:
            return httpx.Response(400, text="private-jira-token")
        assert request.url.params["jql"] == query
        rows = MOCK["filter_issues"](query)
        return httpx.Response(200, json={"startAt": 0, "total": len(rows), "issues": rows[:2]})
    client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(handler))
    assert client.put("/api/jira/settings", headers=HEADERS, json=SETTINGS).status_code == 200
    response = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2, "jql": query})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["connection"]["jql"] == query and all(row["status_name"] == "Open" for row in saved["tickets"])
    assert saved["tickets"][0]["jira_project_key"] == "DEMO"
    rejected = True
    failed = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2, "jql": "invalid query"})
    assert failed.status_code == 400 and "private-jira-token" not in failed.text
    assert client.get("/api/jira/tickets", headers=HEADERS).json() == saved


def test_mock_jql_filters_and_rejects_unsupported_expressions():
    assert len(MOCK["filter_issues"]('project = DEMO AND status = "Open" ORDER BY updated DESC')) == 25
    assert [issue["key"] for issue in MOCK["filter_issues"]('key = DEMO-1')] == ["DEMO-1"]
    assert MOCK["filter_issues"]('text ~ "nonexistent text"') == []
    assert len(MOCK["filter_issues"]('text ~ "final pagination evidence"')) == 75
    for query in ['project = DEMO OR status = Open', 'status in (Open, Done)', 'project = DEMO AND', 'ORDER BY created ASC']:
        with pytest.raises(ValueError):
            MOCK["filter_issues"](query)




def test_mock_filter_examples():
    assert [issue["key"] for issue in MOCK["filter_issues"]('key = DEMO-1')] == ["DEMO-1"]
    assert MOCK["filter_issues"]('text ~ "nonexistent text"') == []
    assert len(MOCK["filter_issues"]('text ~ "final pagination evidence"')) == 75
    for query in ['project = DEMO OR status = Open', 'status in (Open, Done)', 'project = DEMO AND', 'ORDER BY created ASC']:
        with pytest.raises(ValueError):
            MOCK["filter_issues"](query)


def test_export_adds_the_jira_path_to_the_chosen_collection(exporting):
    client, snapshot, state = exporting
    response = export(client, snapshot, collection="support")
    assert response.status_code == 200, response.text
    assert response.json()["collection"] == "support" and state["jira_collection"] == "support"
    paths = [call for call in state["rag_calls"] if call.url.path.endswith("/paths")]
    assert len(paths) == 1 and paths[0].url.path.endswith("/collections/by-name/support/paths")
    assert state["rag_calls"].index(paths[0]) == 0  # the path is in place before anything is read or written
    assert not [call for call in state["rag_calls"] if call.url.path.endswith("/collections")]


def test_export_to_another_collection_moves_the_jira_path(exporting):
    client, snapshot, state = exporting
    assert export(client, snapshot).status_code == 200
    moved = export(client, snapshot, collection="support")
    assert moved.status_code == 200 and moved.json()["status"] == "unchanged"
    assert [call.url.path.split("/by-name/")[1] for call in state["rag_calls"] if call.url.path.endswith("/paths")] == [
        "jira/paths", "support/paths"]
    assert list(state["files"]) == ["rag-data/jira/DEMO-1.md"]  # one folder, now indexed by support


def test_export_fails_with_rag_overlap_message(exporting):
    client, snapshot, state = exporting
    state["rag_error"] = (409, "Path overlaps rag-data in collection support")
    response = export(client, snapshot, collection="support")
    assert response.status_code == 409 and "Path overlaps rag-data in collection support" in response.text
    assert state["files"] == {}


def test_export_fails_when_rag_is_unavailable(exporting, store):
    client, snapshot, state = exporting
    state["rag_error"] = httpx.ConnectError("refused")
    response = export(client, snapshot)
    assert response.status_code == 502 and "RAG service is unavailable. Retry later." in response.text
    assert state["files"] == {}
    with store.connect() as conn:
        assert conn.execute("SELECT 1 FROM ka.audit_events WHERE action='jira.ticket_exported'").fetchone() is None
