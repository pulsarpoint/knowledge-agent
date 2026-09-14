"""Configuration loading: a TOML file of collections, or a bare docs folder."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(Exception):
    pass


VALID_KINDS = ("docs", "code", "memory")


@dataclass
class Collection:
    name: str
    path: Path
    kind: str = "docs"
    db_dir: Path | None = None    # None -> in-folder (today's behavior)


@dataclass
class Config:
    collections: list[Collection]
    llm: dict | None


def load_config(target: Path) -> Config:
    target = Path(target)
    if target.is_dir():
        return Config(
            collections=[Collection(name=target.name, path=target.resolve())],
            llm=None,
        )
    if not target.is_file():
        raise ConfigError(f"config target does not exist: {target}")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {target}: {exc}") from exc
    raw_collections = data.get("collections") or []
    if not isinstance(raw_collections, list):
        raise ConfigError(f"{target}: collections must be an array of tables")
    if not raw_collections:
        raise ConfigError(f"{target}: no [[collections]] entries")
    collections: list[Collection] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_collections):
        if not isinstance(entry, dict):
            raise ConfigError(f"{target}: collections entry {i} must be a table")
        name = entry.get("name")
        path = entry.get("path")
        if not isinstance(name, str) or not isinstance(path, str):
            raise ConfigError(f"{target}: collections entry {i} must have string name and path")
        if not name or not path:
            raise ConfigError(f"{target}: every collection needs name and path")
        if name in seen:
            raise ConfigError(f"{target}: duplicate collection name {name!r}")
        seen.add(name)
        collection_path = Path(path).resolve()
        if not collection_path.is_dir():
            raise ConfigError(f"{target}: path does not exist: {collection_path}")
        kind = entry.get("kind", "docs")
        if kind not in VALID_KINDS:
            raise ConfigError(f"collection '{name}': kind must be one of docs, code, memory")
        db_dir_raw = entry.get("db_dir")
        db_dir: Path | None = None
        if db_dir_raw is not None:
            if not isinstance(db_dir_raw, str):
                raise ConfigError(
                    f"{target}: collection '{name}' db_dir must be a string")
            db_dir = Path(db_dir_raw).resolve()
        collections.append(Collection(name=name, path=collection_path,
                                      kind=kind, db_dir=db_dir))
    llm = data.get("llm")
    if llm is not None and not isinstance(llm, dict):
        raise ConfigError(f"{target}: llm must be a table")
    return Config(collections=collections, llm=llm)


@dataclass
class EmbedConfig:
    base_url: str
    api_key: str | None
    embed_model: str


def resolve_embed(llm_section: dict | None,
                  env: Mapping[str, str] | None = None) -> EmbedConfig | None:
    if env is None:
        env = os.environ
    section = llm_section or {}

    def pick(env_key: str, file_key: str) -> str | None:
        value = env.get(env_key) or section.get(file_key)
        return value if isinstance(value, str) and value else None

    base_url = pick("RAG_EMBED_BASE_URL", "embed_base_url")
    embed_model = pick("RAG_EMBED_MODEL", "embed_model")
    if not (base_url and embed_model):
        return None
    return EmbedConfig(base_url=base_url,
                       api_key=pick("RAG_API_KEY", "api_key"),
                       embed_model=embed_model)


def load_env_file(path: Path | None) -> None:
    from dotenv import load_dotenv

    if path is not None:
        if not Path(path).is_file():
            raise ConfigError(f"env file does not exist: {path}")
        load_dotenv(path, override=False)
        return
    default = Path.cwd() / ".env"
    if default.is_file():
        load_dotenv(default, override=False)
