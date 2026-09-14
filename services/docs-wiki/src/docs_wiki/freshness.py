"""Commits-behind freshness with a small TTL cache. Read-only git."""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

_TTL_SECONDS = 60.0
_cache: dict[tuple[str, str], tuple[float, int | None]] = {}
_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")


def clear_cache() -> None:
    _cache.clear()


def _service_repo(app_root: Path, service: str) -> Path | None:
    # Reject invalid service names: empty, contains path separators, or is . or ..
    if not service or "/" in service or "\\" in service or service in (".", ".."):
        return None

    if service == app_root.name:
        candidate = app_root
    else:
        candidate = app_root / service

    # Belt-and-braces: verify candidate is within app_root
    try:
        if not candidate.resolve().is_relative_to(app_root.resolve()):
            return None
    except (ValueError, RuntimeError):
        return None

    return candidate if (candidate / ".git").exists() else None


def commits_behind(app_root: Path, service: str, sha: str) -> int | None:
    # Validate sha: must be 7-64 lowercase hex chars
    if not _SHA_PATTERN.match(sha):
        return None

    repo = _service_repo(app_root, service)
    if repo is None:
        return None
    key = (str(repo), sha)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-list", "--count", f"{sha}..HEAD"],
            check=True, capture_output=True, text=True, timeout=5)
        value: int | None = int(out.stdout.strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError,
            subprocess.TimeoutExpired):
        value = None
    _cache[key] = (now, value)
    return value


def freshness(app_root: Path | None, analyzed: dict) -> dict[str, int | None]:
    if not isinstance(analyzed, dict):
        return {}
    if app_root is None:
        return {service: None for service in analyzed}
    return {service: commits_behind(app_root, service, str(sha))
            for service, sha in analyzed.items()}


def badge(per_service: dict[str, int | None]) -> str:
    if not per_service:
        return "unknown"
    values = list(per_service.values())
    if any(v is None for v in values):
        return "unknown"
    return "behind" if any(v > 0 for v in values) else "current"
