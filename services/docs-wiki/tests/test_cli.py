import pytest

from docs_wiki import cli


def test_bad_target_exit_2(tmp_path, capsys):
    assert cli.main(["serve", str(tmp_path / "nope")]) == 2
    assert "error" in capsys.readouterr().err.lower()


def test_missing_rag_url_exit_2(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DOCS_WIKI_RAG_URL", raising=False)
    docs = tmp_path / "docs"
    docs.mkdir()
    assert cli.main(["serve", str(docs)]) == 2
    err = capsys.readouterr().err
    assert "error" in err.lower()
    assert "DOCS_WIKI_RAG_URL" in err


def test_serve_builds_app_with_rag_client(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    monkeypatch.setenv("DOCS_WIKI_RAG_URL", "http://rag.local:8790")
    captured = {}

    def fake_run(app, **kwargs):
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    assert cli.main(["serve", str(docs), "--host", "0.0.0.0",
                     "--port", "9999"]) == 0
    assert captured["kwargs"] == {"host": "0.0.0.0", "port": 9999}
    assert captured["app"] is not None


def test_reindex_no_longer_a_subcommand(capsys):
    # The `reindex` subcommand is gone — indexing lives in the separate
    # rag service (`rag index`) now. argparse rejects the unknown
    # subcommand itself (exit code 2, same convention as our own
    # ConfigError-driven exits).
    with pytest.raises(SystemExit) as exc:
        cli.main(["reindex", "/some/docs"])
    assert exc.value.code == 2


def test_help_text_points_to_rag_index(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "rag index" in out
