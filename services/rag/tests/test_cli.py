import base64

from fastapi.testclient import TestClient

from rag import cli


def test_index_checks_a_docs_folder_and_writes_nothing(tmp_path, capsys):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\n## B\n\nBody.\n", encoding="utf-8")
    assert cli.main(["index", str(docs)]) == 0
    assert capsys.readouterr().out == "docs: checked 1\n"
    assert [p.name for p in docs.iterdir()] == ["a.md"]


def test_index_bad_target_exits_2(tmp_path, capsys):
    assert cli.main(["index", str(tmp_path / "missing")]) == 2
    assert "error:" in capsys.readouterr().err


def test_missing_env_file_exits_2(tmp_path, capsys):
    assert cli.main(["serve-workspace", "--env-file", str(tmp_path / "none.env")]) == 2
    assert "env file does not exist" in capsys.readouterr().err


def test_worker_command_runs_the_workspace_worker(monkeypatch):
    from rag import workspace_worker

    calls = []
    monkeypatch.setattr(workspace_worker, "main", lambda: calls.append(True))
    assert cli.main(["worker"]) == 0
    assert calls == [True]


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
    # The legacy SQLite collections API is gone.
    assert client.get("/collections").status_code == 404
    assert client.post("/collections/docs/search", json={"query": "x"}).status_code == 404
    # Current APIs are mounted and require service authentication before DB access.
    assert client.get("/backoffice/workspace").status_code == 403
    assert client.post("/search", json={}).status_code == 403
    for method, path in [("GET", "/status"), ("POST", "/init"), ("GET", "/init/stream")]:
        assert client.request(method, "/rag-backoffice/api" + path).status_code == 404
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob("*")) == [
        "documentation", "documentation/guide.md",
    ]
    assert document.read_text() == "# Guide\n\nExisting content.\n"
