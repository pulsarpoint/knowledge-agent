"""Repository configuration for source browsing."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class Repo:
    name: str
    path: Path
    folder_paths: tuple[str, ...] | None = None


@dataclass
class Config:
    repos: list[Repo]


def load_config(target: Path) -> Config:
    target = Path(target)
    if not target.is_file():
        raise ConfigError(f"config target does not exist: {target}")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {target}: {exc}") from exc

    raw_repos = data.get("repos") or []
    if not isinstance(raw_repos, list):
        raise ConfigError(f"{target}: repos must be an array of tables")
    if not raw_repos and not os.environ.get("CODE_ROOT"):
        raise ConfigError(f"{target}: no [[repos]] entries")

    repos: list[Repo] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_repos):
        if not isinstance(entry, dict):
            raise ConfigError(f"{target}: repos[{i}] must be a table")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{target}: repos[{i}] missing required "
                              "field 'name'")
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise ConfigError(f"{target}: repos[{i}] ({name!r}) missing "
                              "required field 'path'")
        if name in seen:
            raise ConfigError(f"{target}: duplicate repo name {name!r}")
        seen.add(name)
        repo_path = Path(path).resolve()
        if not repo_path.is_dir():
            raise ConfigError(f"{target}: repos[{i}] ({name!r}) path does "
                              f"not exist: {repo_path}")
        repos.append(Repo(name=name, path=repo_path))

    return Config(repos=repos)


def load_env_file(path: Path | None) -> None:
    """Load KEY=VALUE lines into os.environ, never overriding a variable
    already set (os.environ.setdefault per line). An explicitly passed
    path that doesn't exist is a ConfigError; with no path (the CLI's
    default when `--env-file` is omitted), a `.env` in the CURRENT
    WORKING DIRECTORY is loaded if present -- NOT one next to the config
    file, which is a different path unless cwd happens to match
    (copies rag's `load_env_file` rule)."""
    if path is not None:
        path = Path(path)
        if not path.is_file():
            raise ConfigError(f"env file does not exist: {path}")
        _apply_env_file(path)
        return
    default = Path.cwd() / ".env"
    if default.is_file():
        _apply_env_file(default)


def _apply_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
