import copy
import runpy
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from knowledge_admin.jira.client import JiraClient
from test_api import _client
from test_repositories import HEADERS

ISSUES = runpy.run_path(str(Path(__file__).resolve().parents[3] / "deploy/jira-mock/server.py"))["ISSUES"][:7]
SETTINGS = {"base_url": "https://jira.internal/jira/", "username": "mock-admin", "token": "private-jira-token"}


def jira_response(request):
    assert request.headers["Authorization"] == "Bearer private-jira-token"
    assert request.url.host == "jira.internal"
    assert request.url.path.startswith("/jira/rest/api/2/")
    if request.url.path.endswith("/myself"):
        return httpx.Response(200, json={"name": "mock-admin", "displayName": "Mock Administrator", "active": True})
    assert request.url.path.endswith("/search") and request.url.params["jql"] == "ORDER BY updated DESC, key DESC"
    start = int(request.url.params["startAt"])
    size = min(3, int(request.url.params["maxResults"]))
    return httpx.Response(200, json={"startAt": start, "total": len(ISSUES), "issues": copy.deepcopy(ISSUES[start:start + size])})


def setup(store, tmp_path, handler=jira_response):
    client = _client(tmp_path, repository_store=store, jira_transport=httpx.MockTransport(handler))
    response = client.put("/api/jira/settings", headers=HEADERS, json=SETTINGS)
    assert response.status_code == 200, response.text
    return client, response.json()["connection"]


def test_settings_encrypt_preserve_rotate_and_remove_token(store, tmp_path):
    client, config = setup(store, tmp_path)
    assert config["base_url"] == "https://jira.internal/jira" and config["has_token"] and config["revision"] == 1
    with store.connect() as conn:
        secret = conn.execute("SELECT id,ciphertext FROM ka.credentials WHERE kind='jira_pat'").fetchone()
        original = bytes(secret["ciphertext"])
        assert b"private-jira-token" not in original
    saved = {"base_url": config["base_url"], "username": config["username"], "revision": 1}
    assert client.put("/api/jira/settings", headers=HEADERS, json=saved).status_code == 200
    with store.connect() as conn:
        assert bytes(conn.execute("SELECT ciphertext FROM ka.credentials WHERE id=%s", (secret["id"],)).fetchone()["ciphertext"]) == original
    rotated = client.put("/api/jira/settings", headers=HEADERS, json={**saved, "revision": 2, "token": "replacement-jira-token"})
    assert rotated.status_code == 200
    with store.connect() as conn:
        blob = bytes(conn.execute("SELECT ciphertext FROM ka.credentials WHERE id=%s", (secret["id"],)).fetchone()["ciphertext"])
        assert blob != original and b"replacement-jira-token" not in blob
    assert client.put("/api/jira/settings", headers=HEADERS, json=saved).status_code == 409
    removed = client.put("/api/jira/settings", headers=HEADERS, json={**saved, "revision": 3, "token": None})
    assert removed.status_code == 200 and not removed.json()["connection"]["has_token"]
    assert client.post("/api/jira/test", headers=HEADERS).status_code == 400
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.credentials WHERE kind='jira_pat'").fetchone()["n"] == 0
        audit = str(conn.execute("SELECT details FROM ka.audit_events WHERE resource_type='jira_connection'").fetchall())
    for text in [removed.text, rotated.text, client.get("/api/jira/settings", headers=HEADERS).text, audit]:
        assert "private-jira-token" not in text and "replacement-jira-token" not in text and "ciphertext" not in text


def test_connection_checks_search_and_fetches_paginated_snapshot_idempotently(store, tmp_path):
    calls = []
    def handler(request):
        calls.append(request.url)
        return jira_response(request)
    client, config = setup(store, tmp_path, handler)
    result = client.post("/api/jira/test", headers=HEADERS)
    assert result.status_code == 200 and result.json()["user"]["username"] == "mock-admin"
    checked = client.get("/api/jira/settings", headers=HEADERS).json()["connection"]
    assert checked["last_tested_at"] is not None and checked["last_test_error"] is None
    assert client.get("/api/jira/tickets", headers=HEADERS).json()["tickets"] == []
    calls.clear()
    response = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 5})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["tickets"]) == 5 and data["connection"]["last_fetch_total"] == 7
    assert [int(url.params["startAt"]) for url in calls if url.path.endswith("/search")] == [0, 3]
    assert data["tickets"][0]["issue_key"] == "DEMO-1"
    assert client.get("/api/jira/tickets", headers=HEADERS).json() == data
    ids = [ticket["id"] for ticket in data["tickets"]]
    second = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2}).json()
    assert [ticket["id"] for ticket in second["tickets"]] == ids[:2]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.jira_tickets").fetchone()["n"] == 5
        assert conn.execute("SELECT count(*) AS n FROM ka.jira_tickets WHERE out_of_scope_at IS NULL").fetchone()["n"] == 2
    assert "private-jira-token" not in response.text and "raw_payload" not in response.text


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_upstream_failure_is_safe_persisted_and_does_not_replace_tickets(store, tmp_path, status):
    fail = False
    calls = []
    def handler(request):
        calls.append(request)
        if fail:
            return httpx.Response(status, text="secret echoed: private-jira-token", headers={"Location": "https://other.example/steal"})
        return jira_response(request)
    client, config = setup(store, tmp_path, handler)
    original = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2}).json()
    fail = True
    response = client.post("/api/jira/test", headers=HEADERS)
    assert response.status_code in {502, 503} and "private-jira-token" not in response.text
    current = client.get("/api/jira/settings", headers=HEADERS).json()["connection"]
    assert current["last_test_error"] == response.json()["error"]["message"]
    assert client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 3}).status_code in {502, 503}
    current = client.get("/api/jira/tickets", headers=HEADERS).json()
    assert current["tickets"] == original["tickets"] and current["connection"]["last_successful_sync_at"] == original["connection"]["last_successful_sync_at"]
    assert all(request.url.host == "jira.internal" for request in calls)


def test_failed_second_page_keeps_entire_previous_snapshot(store, tmp_path):
    fail = False
    def handler(request):
        if fail and request.url.params.get("startAt") == "3":
            return httpx.Response(200, json={"startAt": 3, "total": 7, "issues": []})
        return jira_response(request)
    client, _ = setup(store, tmp_path, handler)
    original = client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2}).json()
    fail = True
    assert client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 5}).status_code == 502
    assert client.get("/api/jira/tickets", headers=HEADERS).json() == original


@pytest.mark.parametrize("change", ["revision=revision+1", "fetch_revision=fetch_revision+1"])
def test_stale_network_result_cannot_overwrite_newer_settings_or_fetch(store, tmp_path, change):
    def handler(request):
        if request.url.path.endswith("/search"):
            with store.connect() as conn:
                conn.execute(f"UPDATE ka.jira_connections SET {change}")
        return jira_response(request)
    client, _ = setup(store, tmp_path, handler)
    assert client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2}).status_code == 409
    assert client.get("/api/jira/tickets", headers=HEADERS).json()["tickets"] == []


def test_url_change_requires_token_and_does_not_mix_ticket_sources(store, tmp_path):
    client, config = setup(store, tmp_path)
    client.post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": 2})
    changed = {"base_url": "https://other.internal", "username": "mock-admin", "revision": 1}
    assert client.put("/api/jira/settings", headers=HEADERS, json=changed).status_code == 400
    assert len(client.get("/api/jira/tickets", headers=HEADERS).json()["tickets"]) == 2
    assert client.put("/api/jira/settings", headers=HEADERS, json={**changed, "token": "other-token"}).status_code == 200
    data = client.get("/api/jira/tickets", headers=HEADERS).json()
    assert data["tickets"] == [] and data["connection"]["last_successful_sync_at"] is None
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) AS n FROM ka.jira_tickets").fetchone()["n"] == 0


def test_routes_are_admin_only_and_project_scoped(store, tmp_path):
    paths = [("GET", "settings"), ("PUT", "settings"), ("POST", "test"), ("GET", "tickets"), ("POST", "tickets/fetch")]
    client, config = setup(store, tmp_path)
    with store.connect() as conn:
        other = store.project(conn, "foreign")
        conn.execute("UPDATE ka.jira_connections SET project_id=%s,credential_id=NULL WHERE id=%s", (other, UUID(config["id"])))
    for method, suffix in paths:
        assert client.request(method, f"/api/jira/{suffix}", json=SETTINGS if suffix == "settings" else {"limit": 2}).status_code == 403
        assert client.request(method, f"/api/unknown/jira/{suffix}", headers=HEADERS, json=SETTINGS if suffix == "settings" else {"limit": 2}).status_code == 404
    assert client.get("/api/jira/settings", headers=HEADERS).json()["connection"] is None
    assert client.get("/api/jira/tickets", headers=HEADERS).json()["tickets"] == []
    assert client.post("/api/jira/test", headers=HEADERS).status_code == 400
    assert client.put("/api/jira/settings", headers={"X-Forwarded-Groups": "docs-admins"}, json=SETTINGS).status_code == 403


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:token@jira.test", "https://jira.test?token=abc", "https://jira.test#fragment",
    "http://jira.test:99999", "http://jira.test/a/../b", "http://jira.test/%2e%2e", "http://jira.test/\\evil", "not a URL"])
def test_invalid_urls_rejected_without_network(tmp_path, url):
    response = _client(tmp_path).put("/api/jira/settings", headers=HEADERS, json={**SETTINGS, "base_url": url})
    assert response.status_code == 400 and "private-jira-token" not in response.text


@pytest.mark.parametrize("limit", [0, 201, True, 2.5, "25"])
def test_ticket_limit_is_bounded(tmp_path, limit):
    assert _client(tmp_path).post("/api/jira/tickets/fetch", headers=HEADERS, json={"limit": limit}).status_code == 400


@pytest.mark.parametrize("scenario", ["timeout", "html", "oversized", "wrong-user", "inactive", "malformed-issue"])
def test_client_rejects_invalid_upstream_without_exposing_body(scenario):
    def handler(request):
        if scenario == "timeout":
            raise httpx.ReadTimeout("private-jira-token")
        if scenario == "html":
            return httpx.Response(200, text="<html>private-jira-token</html>")
        if scenario == "oversized":
            return httpx.Response(200, text="x" * (4 * 1024 * 1024 + 1))
        if scenario == "wrong-user":
            return httpx.Response(200, json={"name": "someone-else", "active": True})
        if scenario == "inactive":
            return httpx.Response(200, json={"name": "mock-admin", "active": False})
        return httpx.Response(200, json={"total": 1, "startAt": 0, "issues": [{"id": "1", "key": "DEMO-1", "fields": {}}]})
    from fastapi import HTTPException
    with JiraClient("https://jira.internal", "private-jira-token", httpx.MockTransport(handler)) as client:
        with pytest.raises(HTTPException) as error:
            if scenario == "malformed-issue":
                client.tickets(1)
            else:
                client.current_user("mock-admin")
    assert error.value.status_code in {502, 504} and "private-jira-token" not in error.value.detail
