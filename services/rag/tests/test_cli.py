import base64

from fastapi.testclient import TestClient

from rag import cli






def test_index_then_counts(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    assert cli.main(["index", str(docs)]) == 0
    out = capsys.readouterr().out
    assert "indexed 1" in out


def test_index_bad_target_exits_2(tmp_path, capsys):
    assert cli.main(["index", str(tmp_path / "missing.toml")]) == 2
    assert "error:" in capsys.readouterr().err


def test_serve_no_reindex_by_default(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n", encoding="utf-8")
    calls = {}
    monkeypatch.setattr(cli, "_run_server",
                        lambda app, host, port: calls.setdefault("served", True))
    assert cli.main(["serve", str(docs)]) == 0
    assert calls["served"]
    assert not (docs / ".docs-wiki" / "index.db").exists()
    assert cli.main(["serve", str(docs), "--reindex"]) == 0
    assert (docs / ".docs-wiki" / "index.db").exists()


def test_serve_without_embed_prints_bm25_notice(tmp_path, monkeypatch,
                                                capsys):
    # The field incident: serve started with no embed config silently ran
    # BM25-only. The startup notice makes the mode visible in the console.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_run_server", lambda app, host, port: None)
    assert cli.main(["serve", str(docs)]) == 0
    assert "BM25-only" in capsys.readouterr().err


def test_index_with_db_dir_writes_outside_content_folder_and_serve_sees_it(
        tmp_path, capsys, monkeypatch):
    # Regression: the general TOML-config path used to build its
    # Collection with db_dir set but drop it on the floor when calling
    # ingest.index_collection, so `rag index` silently wrote to
    # <path>/.docs-wiki/index.db while `rag serve` (which does honor
    # collection.db_dir via api._indexed/Store.exists) looked at db_dir
    # and reported the collection unindexed.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    db_dir = tmp_path / "elsewhere"
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[collections]]\nname = "docs"\npath = "{docs}"\n'
        f'db_dir = "{db_dir}"\n', encoding="utf-8")

    assert cli.main(["index", str(cfg)]) == 0
    assert "indexed 1" in capsys.readouterr().out
    assert (db_dir / "index.db").is_file()
    assert not (docs / ".docs-wiki").exists()

    captured = {}
    monkeypatch.setattr(
        cli, "_run_server",
        lambda app, host, port: captured.setdefault("app", app))
    assert cli.main(["serve", str(cfg)]) == 0
    client = TestClient(captured["app"])
    body = client.get("/collections").json()
    assert body == [{"name": "docs", "indexed": True, "documents": 1,
                     "retrieval": "bm25-only", "kind": "docs"}]


def test_serve_reindex_with_db_dir_writes_outside_content_folder(
        tmp_path, monkeypatch):
    # Same regression, the `serve --reindex` call site.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    db_dir = tmp_path / "elsewhere"
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[collections]]\nname = "docs"\npath = "{docs}"\n'
        f'db_dir = "{db_dir}"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_run_server", lambda app, host, port: None)

    assert cli.main(["serve", str(cfg), "--reindex"]) == 0
    assert (db_dir / "index.db").is_file()
    assert not (docs / ".docs-wiki").exists()


def test_serve_with_embed_config_has_no_bm25_notice(tmp_path, monkeypatch,
                                                    capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n", encoding="utf-8")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[collections]]\nname = "docs"\npath = "{docs}"\n\n'
        '[llm]\nembed_base_url = "http://embed.local/v1"\n'
        'embed_model = "e"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "_run_server", lambda app, host, port: None)
    assert cli.main(["serve", str(cfg)]) == 0
    assert "BM25-only" not in capsys.readouterr().err
def test_workspace_startup_has_no_legacy_indexes_or_initialization(tmp_path, monkeypatch):
    source = tmp_path / "sources"
    source.mkdir()
    (source / "documentation").mkdir()
    document = source / "documentation" / "guide.md"
    document.write_text("# Guide\n\nExisting content.\n")
    monkeypatch.setenv("RAG_SOURCE_ROOT", str(source))
    monkeypatch.setenv("RG_DATA", str(source))
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("RAG_BACKOFFICE_KEY", "workspace-test-key")
    captured = {}
    monkeypatch.setattr(cli, "_run_server", lambda app, host, port: captured.update(app=app, host=host, port=port))

    assert cli.main(["serve-workspace", "--host", "0.0.0.0", "--port", "8791"]) == 0
    assert (captured["host"], captured["port"]) == ("0.0.0.0", 8791)
    client = TestClient(captured["app"])
    assert client.get("/health").status_code == 200
    assert client.get("/collections").json() == []
    # Current APIs are mounted and require service authentication before DB access.
    assert client.get("/backoffice/workspace").status_code == 403
    assert client.post("/search", json={}).status_code == 403
    for method, path in [("GET", "/status"), ("POST", "/init"), ("GET", "/init/stream")]:
        assert client.request(method, "/rag-backoffice/api" + path).status_code == 404
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob("*")) == [
        "documentation", "documentation/guide.md",
    ]
    assert document.read_text() == "# Guide\n\nExisting content.\n"
