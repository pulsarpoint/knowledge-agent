import subprocess
from pathlib import Path

from docs_wiki import freshness


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def make_repo(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    git(path, "init", "--quiet")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "T")
    (path / "a.txt").write_text("1", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "one")
    return path, git(path, "rev-parse", "HEAD")


def test_current(tmp_path):
    root, sha = make_repo(tmp_path / "app")
    assert freshness.commits_behind(root, "app", sha) == 0
    assert freshness.badge({"app": 0}) == "current"


def test_behind(tmp_path):
    root, sha = make_repo(tmp_path / "app")
    (root / "b.txt").write_text("2", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "two")
    freshness.clear_cache()
    assert freshness.commits_behind(root, "app", sha) == 1
    assert freshness.badge({"app": 1}) == "behind"


def test_unknown_cases(tmp_path):
    root, _sha = make_repo(tmp_path / "app")
    assert freshness.commits_behind(root, "missing-service", "0" * 40) is None
    assert freshness.commits_behind(root, "app", "0" * 40) is None
    assert freshness.freshness(None, {"app": "0" * 40}) == {"app": None}
    assert freshness.badge({"app": None}) == "unknown"


def test_empty_or_garbage_sha_is_unknown(tmp_path):
    root, _sha = make_repo(tmp_path / "app")
    freshness.clear_cache()
    assert freshness.commits_behind(root, "app", "") is None
    assert freshness.commits_behind(root, "app", "--all") is None
    assert freshness.commits_behind(root, "app", "not-hex") is None


def test_traversal_service_is_unknown(tmp_path):
    root, sha = make_repo(tmp_path / "app")
    make_repo(tmp_path / "other")
    freshness.clear_cache()
    assert freshness.commits_behind(root, "../other", sha) is None
    assert freshness.commits_behind(root, "..", sha) is None


def test_missing_git_binary_is_unknown(tmp_path, monkeypatch):
    root, sha = make_repo(tmp_path / "app")
    freshness.clear_cache()
    import subprocess as sp
    def boom(*a, **k):
        raise FileNotFoundError("git")
    monkeypatch.setattr(freshness.subprocess, "run", boom)
    assert freshness.commits_behind(root, "app", sha) is None
