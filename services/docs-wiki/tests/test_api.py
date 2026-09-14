import asyncio
import hashlib
import json

import httpx
import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from docs_wiki import api as api_mod, audit, gaps, webapp
from docs_wiki.config import App, Config, ServerConfig
from docs_wiki.rag_client import RagClient
from docs_wiki.webapp import create_app


@pytest.fixture()
def rag(fake_rag):
    """A RagClient wired to `fake_rag`, pre-seeded with one "shop"
    collection's catalog/document/search fixtures — byte-compatible with
    what the old local-index `corpus` fixture produced, so response-shape
    assertions carry over unchanged."""
    transport, state = fake_rag
    state["documents"]["shop"] = [
        {"path": "how-to/refunds.md", "title": "Refunds",
         "quadrant": "how-to", "front_matter": {"updated": "2026-08-01"}},
        {"path": "index.md", "title": "Overview", "quadrant": "index",
         "front_matter": {}},
    ]
    state["contents"][("shop", "how-to/refunds.md")] = {
        "path": "how-to/refunds.md", "title": "Refunds", "quadrant": "how-to",
        "front_matter": {"updated": "2026-08-01"},
        "markdown": "# Refunds\n\n## Requesting\nRefunds are asynchronous.\n",
        "headings": [{"text": "Requesting", "anchor": "requesting",
                      "level": 2}],
    }
    state["search_results"]["shop"] = [
        {"path": "how-to/refunds.md", "title": "Refunds",
         "heading_path": "Requesting", "anchor": "requesting",
         "text": "Refunds are asynchronous.", "score": 1.0},
    ]
    client = RagClient("http://rag.test", transport=transport)
    return client, state


def _server(tmp_path, **overrides):
    kwargs = dict(cors_origin=None, session_retention_days=90,
                  state_dir=str(tmp_path / "state"))
    kwargs.update(overrides)
    return ServerConfig(**kwargs)


def _client(tmp_path, rag_client=None, **kwargs):
    cfg = Config(apps=[App(name="shop", app_root=None)], llm=None)
    kwargs.setdefault("server", _server(tmp_path))
    kwargs.setdefault("rag_client", rag_client)
    return TestClient(create_app(cfg, **kwargs))


def test_toc_groups_by_quadrant(tmp_path, rag):
    rag_client, _state = rag
    client = _client(tmp_path, rag_client=rag_client)
    body = client.get("/api/shop/toc").json()
    labels = [g["group"] for g in body]
    assert "How-to — do something" in labels
    howto = next(g for g in body if g["group"].startswith("How-to"))
    assert {"title": "Refunds", "path": "how-to/refunds.md",
            "freshness": "unknown"} in howto["docs"]


def test_toc_rag_error_returns_502(tmp_path, rag):
    rag_client, state = rag
    state["fail"] = True
    client = _client(tmp_path, rag_client=rag_client)
    response = client.get("/api/shop/toc")
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_toc_unknown_quadrant_falls_back_to_raw_label(tmp_path, rag):
    # A future/skewed rag service could emit a quadrant GROUP_LABELS
    # doesn't know about; that must group under the raw quadrant string
    # as its own label rather than KeyError into a raw 500 — never-500
    # is a hard constraint for these routes.
    rag_client, state = rag
    state["documents"]["shop"].append(
        {"path": "misc/oddball.md", "title": "Oddball",
         "quadrant": "future-quadrant", "front_matter": {}})
    client = _client(tmp_path, rag_client=rag_client)
    response = client.get("/api/shop/toc")
    assert response.status_code == 200
    body = response.json()
    groups = {g["group"]: g["docs"] for g in body}
    assert "future-quadrant" in groups
    assert {"title": "Oddball", "path": "misc/oddball.md",
            "freshness": "unknown"} in groups["future-quadrant"]


def test_doc_returns_markdown_and_headings(tmp_path, rag):
    rag_client, _state = rag
    client = _client(tmp_path, rag_client=rag_client)
    body = client.get("/api/shop/doc", params={"path": "how-to/refunds.md"}).json()
    assert body == {
        "path": "how-to/refunds.md", "title": "Refunds",
        "markdown": "# Refunds\n\n## Requesting\nRefunds are asynchronous.\n",
        "front_matter": {"updated": "2026-08-01"},
        "freshness": "unknown",
        "headings": [{"text": "Requesting", "anchor": "requesting",
                      "level": 2}],
    }


def test_doc_unknown_path_404(tmp_path, rag):
    rag_client, _state = rag
    client = _client(tmp_path, rag_client=rag_client)
    response = client.get("/api/shop/doc", params={"path": "nope.md"})
    assert response.status_code == 404
    assert response.json()["error"]["message"]


def test_doc_rag_error_returns_502(tmp_path, rag):
    rag_client, state = rag
    state["fail"] = True
    client = _client(tmp_path, rag_client=rag_client)
    response = client.get("/api/shop/doc",
                          params={"path": "how-to/refunds.md"})
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_doc_missing_path_400(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/shop/doc")
    assert response.status_code == 400
    assert response.json()["error"]["message"]


def test_unknown_app_404(tmp_path):
    assert _client(tmp_path).get("/api/ghost/toc").status_code == 404


def test_search_returns_hits(tmp_path, rag):
    rag_client, _state = rag
    client = _client(tmp_path, rag_client=rag_client)
    hits = client.get("/api/shop/search", params={"q": "asynchronous"}).json()
    assert hits == [{"path": "how-to/refunds.md", "title": "Refunds",
                     "heading_path": "Requesting",
                     "snippet": "Refunds are asynchronous.",
                     "anchor": "requesting"}]
    assert "score" not in hits[0]


def test_search_requests_top_20(tmp_path, rag):
    # Phase-4's local search (indexer.search) returned up to limit=20
    # hits; rag_client.search's own default is top=8, so the API layer
    # must pass top=20 explicitly at the call site to stay
    # byte-compatible with the old result-count ceiling.
    rag_client, state = rag
    client = _client(tmp_path, rag_client=rag_client)
    client.get("/api/shop/search", params={"q": "asynchronous"})
    assert state["search_requests"][-1]["top"] == 20


def test_search_empty_query_short_circuits(tmp_path, rag):
    rag_client, state = rag
    client = _client(tmp_path, rag_client=rag_client)
    assert client.get("/api/shop/search", params={"q": ""}).json() == []
    assert state["search_requests"] == []


def test_search_rag_error_returns_502(tmp_path, rag):
    rag_client, state = rag
    state["fail"] = True
    client = _client(tmp_path, rag_client=rag_client)
    response = client.get("/api/shop/search", params={"q": "asynchronous"})
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_toc_without_rag_configured_returns_502(tmp_path):
    # rag_client is None whenever create_app runs with no injected client
    # and no resolvable [rag] url (webapp.py leaves it unset rather than
    # raising) — the read routes must guard against this themselves
    # rather than dereferencing None and turning it into a raw 500.
    client = _client(tmp_path)
    response = client.get("/api/shop/toc")
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_doc_without_rag_configured_returns_502(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/shop/doc",
                          params={"path": "how-to/refunds.md"})
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_search_without_rag_configured_returns_502(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/shop/search", params={"q": "asynchronous"})
    assert response.status_code == 502
    assert "documentation service unavailable" in response.json()["error"]["message"]


def test_gaps_endpoint(tmp_path):
    # gaps live under the central state dir (<state_dir>/<app>), not any
    # docs folder — see docs_wiki.webapp.app_state_dir.
    state_dir = tmp_path / "state" / "shop"
    gaps.append_gap(state_dir, "what about taxes?", [])
    (state_dir / "gaps.jsonl").open("a").write("not json\n")
    body = _client(tmp_path).get("/api/shop/gaps").json()
    assert body["skipped"] == 1
    assert body["gaps"][0]["question"] == "what about taxes?"


def test_gaps_land_under_state_dir_with_results_key(tmp_path):
    # gaps.jsonl lives directly under the app's state dir (no ".docs-wiki"
    # subfolder, and no dependency on a docs folder at all), and the
    # entry gains a `results` telemetry key when the caller supplies one.
    # `top_score` is omitted rather than written as null when unavailable
    # (extra keys only "when available").
    state_dir = tmp_path / "state" / "shop"
    assert not state_dir.exists()
    gaps.append_gap(state_dir, "what about vat?", [], results=3)
    assert (state_dir / "gaps.jsonl").is_file()
    entries, skipped = gaps.load_gaps(state_dir)
    assert skipped == 0
    assert entries[0]["question"] == "what about vat?"
    assert entries[0]["results"] == 3
    assert "top_score" not in entries[0]


def test_append_gap_session_ref_extra_key_only_when_given(tmp_path):
    state_dir = tmp_path / "state" / "shop"
    gaps.append_gap(state_dir, "q1", [])
    gaps.append_gap(state_dir, "q2", [], session_ref="abc123")
    entries, skipped = gaps.load_gaps(state_dir)
    assert skipped == 0
    assert "session_ref" not in entries[0]
    assert entries[1]["session_ref"] == "abc123"


def test_load_gaps_folds_matching_code_answer(tmp_path):
    state_dir = tmp_path / "state" / "shop"
    gaps.append_gap(state_dir, "what about taxes?", [], session_ref="abc123")
    gaps.append_code_answer(
        state_dir, "what about taxes?", "abc123",
        "Taxes are computed in billing.py.",
        [{"file": "billing.py", "lines": "1-2", "quote": "compute_tax()"}],
        False)
    entries, skipped = gaps.load_gaps(state_dir)
    assert skipped == 0
    assert len(entries) == 1
    assert entries[0]["code_answer"]["answer"] == \
        "Taxes are computed in billing.py."
    assert entries[0]["code_answer"]["not_found"] is False
    assert entries[0]["code_answer"]["citations"][0]["file"] == "billing.py"


def test_load_gaps_unmatched_code_answer_counts_as_skipped(tmp_path):
    state_dir = tmp_path / "state" / "shop"
    gaps.append_code_answer(state_dir, "no matching gap", "zzz",
                            "answer", [], False)
    entries, skipped = gaps.load_gaps(state_dir)
    assert entries == []
    assert skipped == 1


def test_load_gaps_folds_onto_last_matching_gap(tmp_path):
    # Two NOT_COVERED gaps share question+session_ref (e.g. the same
    # question was asked twice in one conversation) — the code-answer
    # must attach to the LAST prior one, not the first.
    state_dir = tmp_path / "state" / "shop"
    gaps.append_gap(state_dir, "q", [], session_ref="s1")
    gaps.append_gap(state_dir, "q", [], session_ref="s1")
    gaps.append_code_answer(state_dir, "q", "s1", "answer", [], False)
    entries, skipped = gaps.load_gaps(state_dir)
    assert skipped == 0
    assert "code_answer" not in entries[0]
    assert entries[1]["code_answer"]["answer"] == "answer"


def test_cors_enabled_only_when_configured(tmp_path):
    server = _server(tmp_path, cors_origin="http://localhost:5173")
    on = _client(tmp_path, server=server)
    response = on.get("/api/apps", headers={"Origin": "http://localhost:5173"})
    assert response.headers.get("access-control-allow-origin") \
        == "http://localhost:5173"
    off = _client(tmp_path)
    response = off.get("/api/apps", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in response.headers


import re

from docs_wiki import sessions
from docs_wiki.code_client import CodeAgentClient
from docs_wiki.config import resolve_llm

LLM = {"base_url": "http://llm.local/v1", "chat_model": "c"}


def _sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines()
                     if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture()
def live_client(tmp_path, fake_llm, rag):
    import httpx
    llm_transport, llm_state = fake_llm
    rag_client, rag_state = rag
    llm_cfg = resolve_llm(LLM, env={})
    server = _server(tmp_path)
    app = create_app(Config(apps=[App(name="shop", app_root=None)], llm=None),
                     server=server,
                     rag_client=rag_client)
    state_dir = webapp.app_state_dir(server, "shop")
    return TestClient(app), llm_state, state_dir, rag_state


@pytest.fixture()
def live_client_with_code(tmp_path, fake_llm, rag, fake_code_agent):
    # Same wiring as `live_client` plus a code_client backed by
    # `fake_code_agent` — the escalation route's own dedicated client,
    # kept separate from `live_client` so every other test stays
    # unaffected by (and unaware of) the code-agent's existence.
    import httpx
    llm_transport, llm_state = fake_llm
    rag_client, rag_state = rag
    code_transport, code_state = fake_code_agent
    llm_cfg = resolve_llm(LLM, env={})
    code_client = CodeAgentClient("http://code.test", transport=code_transport)
    server = _server(tmp_path)
    app = create_app(Config(apps=[App(name="shop", app_root=None)], llm=None),
                     server=server,
                     rag_client=rag_client, code_client=code_client)
    state_dir = webapp.app_state_dir(server, "shop")
    return TestClient(app), llm_state, state_dir, rag_state, code_state


def test_chat_rejects_unissued_session_key(live_client):
    # A well-formed key that the server never minted must not be allowed
    # to silently create a conversation under that client-chosen name —
    # the server is the sole issuer of session keys (mirrors the 404s
    # GET/DELETE /chats/{key} already give for an unknown key).
    client, _state, state_dir, _rag_state = live_client
    response = client.post("/api/shop/chat",
                           json={"question": "hi",
                                 "session": "teamchat1-unissued-key"})
    assert response.status_code == 404
    assert response.json()["error"]["message"]
    assert not sessions.exists(state_dir, "teamchat1-unissued-key")


def test_chat_body_validation_returns_error_shape(live_client):
    # FastAPI's own 422 {"detail": [...]} would violate the /api 4xx
    # contract of {"error": {"message": ...}}; both a type mismatch and
    # a wholly malformed JSON body must land on our shape at 400.
    client, _state, _state_dir, _rag_state = live_client
    bad_type = client.post("/api/shop/chat", json={"question": 123})
    assert bad_type.status_code == 400
    assert bad_type.json()["error"]["message"]
    bad_json = client.post("/api/shop/chat", content=b"not json",
                           headers={"content-type": "application/json"})
    assert bad_json.status_code == 400
    assert bad_json.json()["error"]["message"]


def test_validation_handler_leaves_non_api_routes_on_default_422(tmp_path):
    # Non-/api routes (the server-rendered pages) must keep FastAPI's own
    # {"detail": [...]} 422 shape — only /api is bound to the
    # {"error": {"message": ...}} contract. request_validation_exception_
    # handler (FastAPI's default) never actually reads the request, so a
    # minimal stand-in with just a `.url.path` is sufficient here to
    # exercise the /api-vs-not branch in the handler we registered.
    app = create_app(Config(apps=[App(name="shop", app_root=None)],
                            llm=None),
                     server=_server(tmp_path))
    handler = app.exception_handlers[RequestValidationError]
    exc = RequestValidationError(errors=[
        {"type": "missing", "loc": ("body", "q"), "msg": "Field required",
         "input": None}])

    class _FakeURL:
        path = "/shop/ask"

    class _FakeRequest:
        url = _FakeURL()

    response = asyncio.run(handler(_FakeRequest(), exc))
    assert response.status_code == 422
    body = json.loads(bytes(response.body))
    assert isinstance(body["detail"], list)


# --- POST /{app}/chat/{session_key}/escalate --------------------------------


def test_escalate_unknown_session_404(live_client_with_code):
    client, *_rest = live_client_with_code
    response = client.post(
        "/api/shop/chat/unknownkey1234567890/escalate",
        json={"question": "hi"})
    assert response.status_code == 404


def test_escalate_unknown_app_404(live_client_with_code):
    client, *_rest = live_client_with_code
    response = client.post(
        "/api/ghost/chat/unknownkey1234567890/escalate",
        json={"question": "hi"})
    assert response.status_code == 404


# --- source selection: ChatRequest.sources + /api/apps capability discovery -
#
# See .superpowers/sdd/2026-08-05-chat-source-selection/task-2-brief.md.
# CODE_COLLECTION_ROW mirrors the REAL /collections response shape
# (services/rag/src/rag/api.py list_collections: name/indexed/documents/
# retrieval/kind) rather than a narrowed test-only subset, per the
# controller resolution binding this task.

CODE_COLLECTION_ROW = {"name": "shop-code", "indexed": True, "documents": 3,
                       "retrieval": "hybrid", "kind": "code"}


def test_apps_sources_docs_only(tmp_path, fake_llm, rag):
    llm_transport, _llm_state = fake_llm
    rag_client, _rag_state = rag
    llm_cfg = resolve_llm(LLM, env={})
    client = _client(tmp_path, rag_client=rag_client)
    body = client.get("/api/apps").json()
    assert body[0]["sources"] == {"docs": True, "codex": False}


def test_apps_sources_full_config(tmp_path, fake_llm, rag, fake_code_agent):
    llm_transport, _llm_state = fake_llm
    rag_client, rag_state = rag
    rag_state["collections"] = [CODE_COLLECTION_ROW]
    code_transport, _code_state = fake_code_agent
    code_client = CodeAgentClient("http://code.test", transport=code_transport)
    llm_cfg = resolve_llm(LLM, env={})
    client = _client(tmp_path, rag_client=rag_client,
                     code_client=code_client)
    body = client.get("/api/apps").json()
    assert body[0]["sources"] == {"docs": True, "codex": True}


def test_apps_sources_all_false_when_ask_not_configured(tmp_path):
    # ask_enabled False (no llm/rag) must force every source off in the
    # capability report, regardless of code_client configuration.
    client = _client(tmp_path)
    body = client.get("/api/apps").json()
    assert body[0]["sources"] == {"docs": False, "codex": False}


# --- degenerate all-sources-off rejection (controller resolution #2) --------


def test_chat_docs_off_alone_is_legitimate_when_codex_available(
        live_client_with_code):
    # docs-off alone is legitimate (e.g. codex-only testing) as long as
    # another source is effectively available — must NOT 400.
    client, state, _state_dir, _rag_state, _code_state = live_client_with_code
    state["chat_script"] = [{"text": "codex-only answer [1]."}]
    response = client.post("/api/shop/chat",
                           json={"question": "hi", "sources": {"docs": False}})
    assert response.status_code == 200


# --- ChatRequest.sources typing (controller resolution #1) ------------------

def test_chat_sources_non_bool_value_rejected(live_client):
    client, _state, _state_dir, _rag_state = live_client
    response = client.post("/api/shop/chat",
                           json={"question": "hi",
                                 "sources": {"docs": "toggle"}})
    assert response.status_code == 400
    assert response.json()["error"]["message"]


def test_chat_sources_unknown_keys_are_ignored(live_client):
    client, state, _state_dir, _rag_state = live_client
    state["chat_script"] = [{"text": "ok [1]."}]
    response = client.post("/api/shop/chat",
                           json={"question": "hi",
                                 "sources": {"unknown-thing": True}})
    assert response.status_code == 200


# --- audit writes on chat done + escalation --------------------------------


def test_reader_does_not_advertise_admin_capabilities(tmp_path):
    client = _client(tmp_path)
    for headers in ({}, {"X-Forwarded-Groups": "docs-admins"}):
        assert "is_admin" not in client.get("/api/apps", headers=headers).json()[0]


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/admin/backoffice/status"),
    ("POST", "/api/admin/backoffice/init"),
    ("GET", "/api/admin/backoffice/init/stream"),
    ("GET", "/api/shop/admin/audit"),
    ("GET", "/api/shop/admin/audit/users"),
    ("GET", "/api/shop/admin/audit/users/a@example.test/sessions"),
    ("GET", "/api/shop/admin/audit/session/k1"),
    ("GET", "/shop/admin/platform"),
])
def test_reader_has_no_admin_routes_even_for_admins(tmp_path, method, path):
    client = _client(tmp_path)
    response = client.request(method, path, headers={"X-Forwarded-Groups": "docs-admins"})
    assert response.status_code == 404


# --- GET /{app_name}/chats (per-user session list) ---------------------------


def test_my_chats_empty_without_identity(live_client):
    client = live_client[0]
    assert client.get("/api/shop/chats").json() == {"sessions": []}


def test_my_chats_unknown_app_404(tmp_path):
    response = _client(tmp_path).get(
        "/api/ghost/chats", headers={"X-Forwarded-Email": "a@x.io"})
    assert response.status_code == 404


def test_my_chats_capped_at_50(tmp_path, monkeypatch):
    fake = [{"session_key": f"k{i}", "first_question": f"q{i}",
             "questions": 1, "first_ts": "t", "last_ts": "t"}
            for i in range(60)]
    monkeypatch.setattr(api_mod.audit, "sessions_for", lambda *_: fake)
    monkeypatch.setattr(api_mod.sessions, "exists", lambda *_: True)
    client = _client(tmp_path)
    mine = client.get("/api/shop/chats",
                      headers={"X-Forwarded-Email": "a@x.io"}).json()["sessions"]
    assert len(mine) == 50
    assert mine[0]["session_key"] == "k0"


def test_legacy_chat_and_escalation_are_todo_without_execution(tmp_path, monkeypatch):
    import asyncio
    import httpx
    async def forbidden(*args, **kwargs):
        pytest.fail('TODO must not call a model or source')
    monkeypatch.setattr(httpx.AsyncClient, 'send', forbidden)
    server = _server(tmp_path)
    client = TestClient(create_app(Config(apps=[App(name='shop', app_root=None)], llm=None), server=server))
    directory = webapp.app_state_dir(server, 'shop')
    for _ in range(2):
        response = client.post('/api/shop/chat', json={'question': 'Inspect'})
        assert response.status_code == 200 and response.text == 'TODO'
        assert response.headers['content-type'].startswith('text/plain')
    assert not directory.exists()
    key = 'old-session-key'
    async def seed():
        await sessions.open_session(directory, key).add_items([{'role': 'user', 'content': 'Old question'}])
    asyncio.run(seed())
    before = (directory / 'sessions.db').read_bytes()
    response = client.post(f'/api/shop/chat/{key}/escalate', json={'question': 'Inspect'})
    assert response.status_code == 200 and response.text == 'TODO'
    assert client.get(f'/api/shop/chats/{key}').json()['messages'] == [{'role': 'user', 'content': 'Old question'}]
    assert (directory / 'sessions.db').read_bytes() == before
    assert client.post('/api/missing/chat', json={'question': 'Inspect'}).status_code == 404
    assert client.post('/api/shop/chat/missing-key/escalate', json={'question': 'Inspect'}).status_code == 404
    for question in [' ', 'x' * 10001]:
        assert client.post('/api/shop/chat', json={'question': question}).status_code == 400
        assert client.post(f'/api/shop/chat/{key}/escalate', json={'question': question}).status_code == 400
