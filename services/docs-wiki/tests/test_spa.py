import pytest
from fastapi.testclient import TestClient

from docs_wiki import webapp
from docs_wiki.config import App, Config, ServerConfig
from docs_wiki.webapp import create_app


@pytest.fixture()
def docs(tmp_path):
    # SPA serving/routing is entirely independent of the RAG rewire — this
    # fixture just needs a stable tmp dir to key the app's name/state on,
    # not a real docs folder (docs-wiki never reads one anymore).
    folder = tmp_path / "docs"
    folder.mkdir()
    return folder


@pytest.fixture()
def ui_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>docs-wiki SPA</title>",
                                     encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log('app')",
                                                   encoding="utf-8")
    (dist / "favicon.ico").write_text("icon", encoding="utf-8")
    return dist


def _client(docs, ui_dist=None):
    cfg = Config(apps=[App(name="shop", app_root=None)], llm=None)
    server = ServerConfig(cors_origin=None, session_retention_days=90,
                          ui_dist=str(ui_dist) if ui_dist else None,
                          state_dir=str(docs.parent / "state"))
    return TestClient(create_app(cfg, server=server))


def test_serves_index_html_for_spa_routes(docs, ui_dist):
    client = _client(docs, ui_dist)
    for path in ["/shop/chat/abc123", "/unknown"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "docs-wiki SPA" in response.text
        assert response.headers["cache-control"] == "no-cache"


def test_former_htmx_urls_now_serve_spa(docs, ui_dist):
    client = _client(docs, ui_dist)
    for path in ["/", "/shop/", "/shop/doc/how-to/refunds.md",
                 "/shop/search", "/shop/gaps"]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert "docs-wiki SPA" in response.text, path


def test_serves_hashed_assets_with_long_cache(docs, ui_dist):
    client = _client(docs, ui_dist)
    response = client.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]
    plain = client.get("/favicon.ico")
    assert plain.status_code == 200
    assert "immutable" not in plain.headers.get("cache-control", "")


def test_null_byte_path_falls_back_to_index_not_500(docs, ui_dist):
    # %00 decodes to a literal null byte in `full_path` once routing
    # unquotes it, and Path.resolve() raises ValueError on an embedded
    # null character — without a guard this reaches the ASGI app as an
    # unhandled 500, violating the "failures fall to index.html" contract.
    # TestClient/httpx accepts this URL unmodified (no client-side
    # rejection to note here).
    client = _client(docs, ui_dist)
    response = client.get("/assets/app-abc123.js%00.png")
    assert response.status_code == 200
    assert "docs-wiki SPA" in response.text


def test_api_wins_over_spa(docs, ui_dist):
    client = _client(docs, ui_dist)
    body = client.get("/api/apps").json()
    assert body[0]["name"] == "shop"


def test_unknown_api_path_is_json_error_not_html(docs, ui_dist):
    client = _client(docs, ui_dist)
    response = client.get("/api/shop/nonexistent")
    assert response.status_code == 404
    assert response.json()["error"]["message"]
    post = client.post("/api/nope")
    assert post.status_code == 404
    assert post.json()["error"]["message"]


def test_non_get_on_spa_route_is_json_404_not_index_html(docs, ui_dist):
    # A SPA never POSTs/PUTs/etc. to itself, so a non-GET request to a
    # client-side route (or anywhere else under the catch-all) gets the
    # same JSON 404 error shape as an unknown /api path, not index.html —
    # while GET on the same path keeps serving the SPA normally.
    client = _client(docs, ui_dist)
    post = client.post("/some/spa/route")
    assert post.status_code == 404
    assert post.json()["error"]["message"]
    get = client.get("/some/spa/route")
    assert get.status_code == 200
    assert "docs-wiki SPA" in get.text


def test_traversal_attempts_fall_back_to_index(docs, ui_dist, tmp_path):
    # NOTE on what these probes actually exercise: httpx (TestClient's HTTP
    # layer) collapses dot-segments while parsing the URL, before the
    # request is ever sent — this happens identically for client.get(...)
    # and client.request("GET", ...), and for httpx.URL(...) directly.
    # `/../secret.txt` and `/assets/../../secret.txt` are both normalized
    # to `/secret.txt` client-side, so the route's `full_path` is just
    # "secret.txt" — they assert real behavior (no leak) but never drive a
    # ".." into `_resolve_ui_dist`'s guard. `/%2e%2e/secret.txt` is the one
    # probe that does: the percent-encoding survives httpx's dot-segment
    # collapse, and Starlette's path converter decodes it into a literal
    # ".." after routing, so `full_path == "../secret.txt"` reaches the
    # route and is caught by `candidate.is_relative_to(ui_dist)`. See
    # test_traversal_via_symlink_is_blocked below for an unambiguous,
    # encoding-independent exercise of that same guard.
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret", encoding="utf-8")
    client = _client(docs, ui_dist)
    for path in ["/../secret.txt", "/assets/../../secret.txt",
                 "/%2e%2e/secret.txt"]:
        response = client.get(path)
        assert "s3cret" not in response.text, path


def test_traversal_via_symlink_is_blocked(docs, ui_dist, tmp_path):
    # Exercises the same `candidate.resolve()` + `is_relative_to(ui_dist)`
    # guard as above, but via a request path with no dot-segments at all
    # ("/escape.txt"), so nothing here depends on how any HTTP client
    # parses or normalizes URLs — the traversal is in the filesystem, not
    # the URL, and reaches the route exactly as written.
    secret = tmp_path / "secret.txt"
    secret.write_text("s3cret", encoding="utf-8")
    (ui_dist / "escape.txt").symlink_to(secret)
    client = _client(docs, ui_dist)
    response = client.get("/escape.txt")
    assert "s3cret" not in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_no_build_shows_informational_page(docs, monkeypatch):
    # Force resolution to "no build" regardless of whether a developer has
    # actually run `pnpm build` in the sibling services/docs-wiki-ui
    # checkout — `_resolve_ui_dist`'s real fallback chain checks that
    # sibling path on disk, so without this monkeypatch this test's result
    # depends on the state of a directory outside this test's control.
    monkeypatch.setattr(webapp, "_resolve_ui_dist", lambda server: None)
    client = _client(docs, ui_dist=None)
    response = client.get("/some/spa/route")
    assert response.status_code == 200
    assert "no UI build" in response.text
    assert client.get("/api/apps").status_code == 200


def test_resolve_ui_dist_finds_sibling_build(monkeypatch, tmp_path):
    # Pins the auto-detection chain itself (spec §5: explicit config/env →
    # packaged ui/ → sibling repo build) — previously only exercised
    # end-to-end via the `ui_dist` fixture, which always takes the
    # explicit-config branch and never reached the sibling fallback.
    fake_services_dir = tmp_path / "services"
    sibling_dist = fake_services_dir / "docs-wiki-ui" / "build" / "client"
    sibling_dist.mkdir(parents=True)
    (sibling_dist / "index.html").write_text("<!doctype html>",
                                             encoding="utf-8")

    # `_PACKAGE_DIR` sits at services/docs-wiki/src/docs_wiki in the real
    # layout; `_resolve_ui_dist` derives the sibling candidate as
    # `_PACKAGE_DIR.parents[2] / "docs-wiki-ui" / "build" / "client"`, so
    # reproduce that ancestry under `fake_services_dir` (these intermediate
    # directories need not exist — only the final candidate does).
    monkeypatch.setattr(webapp, "_PACKAGE_DIR",
                        fake_services_dir / "docs-wiki" / "src" / "docs_wiki")

    server = ServerConfig(cors_origin=None, session_retention_days=90)
    assert webapp._resolve_ui_dist(server) == sibling_dist.resolve()


def test_traversal_to_index_via_assets_prefix_keeps_no_cache(docs, ui_dist):
    # `/assets/%2e%2e/index.html` resolves to `ui_dist/index.html` — not
    # under `ui_dist/assets` — even though the raw request path starts
    # with "assets/". The immutable, one-year cache header is meant only
    # for hashed build assets; applying it here would tell every client to
    # cache the app shell (index.html) permanently.
    client = _client(docs, ui_dist)
    response = client.get("/assets/%2e%2e/index.html")
    assert response.status_code == 200
    assert "docs-wiki SPA" in response.text
    assert "immutable" not in response.headers.get("cache-control", "")
