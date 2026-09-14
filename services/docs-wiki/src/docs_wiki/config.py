"""Configuration loading: a TOML file of apps, or a bare docs folder."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(Exception):
    pass


@dataclass
class App:
    name: str
    # docs-wiki never indexes locally: the RAG service owns each app's
    # documentation folder under its own `[[collections]]` config. A
    # `docs` key on `[[apps]]` is rejected unconditionally — see below.
    app_root: Path | None


@dataclass
class Config:
    apps: list[App]
    llm: dict | None
    server: dict | None = None
    rag: dict | None = None
    code_agent: dict | None = None


def load_config(target: Path) -> Config:
    target = Path(target)
    if target.is_dir():
        return Config(
            apps=[App(name=target.name, app_root=None)],
            llm=None,
        )
    if not target.is_file():
        raise ConfigError(f"config target does not exist: {target}")
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {target}: {exc}") from exc
    raw_apps = data.get("apps") or []
    if not isinstance(raw_apps, list):
        raise ConfigError(f"{target}: apps must be an array of tables")
    if not raw_apps:
        raise ConfigError(f"{target}: no [[apps]] entries")
    raw_rag = data.get("rag")
    if raw_rag is not None and not isinstance(raw_rag, dict):
        raise ConfigError(f"{target}: rag must be a table")
    raw_code_agent = data.get("code_agent")
    if raw_code_agent is not None and not isinstance(raw_code_agent, dict):
        raise ConfigError(f"{target}: code_agent must be a table")
    apps: list[App] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_apps):
        if not isinstance(entry, dict):
            raise ConfigError(f"{target}: apps entry {i} must be a table")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{target}: apps entry {i} must have a string name")
        if "docs" in entry:
            # docs-wiki never reads a local docs folder for an app: the
            # RAG service owns indexing (its own `[[collections]]`
            # config), unconditionally — no legacy local-docs mode.
            raise ConfigError(
                f"app '{name}': docs moved to the RAG service config")
        if name in seen:
            raise ConfigError(f"{target}: duplicate app name {name!r}")
        seen.add(name)
        app_root = entry.get("app_root")
        root_path: Path | None = None
        if app_root is not None:
            if not isinstance(app_root, str):
                raise ConfigError(f"{target}: apps entry {i} app_root must be a string")
            root_path = Path(app_root).resolve()
            if not root_path.is_dir():
                raise ConfigError(
                    f"{target}: app_root does not exist: {root_path}")
        apps.append(App(name=name, app_root=root_path))
    llm = data.get("llm")
    if llm is not None and not isinstance(llm, dict):
        raise ConfigError(f"{target}: llm must be a table")
    server = data.get("server")
    if server is not None and not isinstance(server, dict):
        raise ConfigError(f"{target}: server must be a table")
    return Config(apps=apps, llm=llm, server=server, rag=raw_rag,
                 code_agent=raw_code_agent)


@dataclass
class LlmConfig:
    base_url: str | None
    api_key: str | None
    chat_model: str
    reasoning_effort: str | None = None
    # Per-question ceiling on model round-trips (each tool call costs
    # one). None -> the agent's own defaults (docs-only vs code-tools
    # turns differ; see agent.MAX_TURNS / MAX_TURNS_CODE).
    max_turns: int | None = None
    provider: str = 'openai'
    api_format: str = 'chat_completions'
    aws_region: str | None = None


def resolve_llm(llm_section: dict | None,
                env: Mapping[str, str] | None = None) -> LlmConfig | None:
    if env is None:
        env = os.environ
    section = llm_section or {}

    def pick(env_key: str, file_key: str) -> str | None:
        value = env.get(env_key) or section.get(file_key)
        return value if isinstance(value, str) and value else None

    base_url = pick("DOCS_WIKI_LLM_BASE_URL", "base_url")
    chat_model = pick("DOCS_WIKI_CHAT_MODEL", "chat_model")
    if not (base_url and chat_model):
        return None
    return LlmConfig(
        base_url=base_url,
        api_key=pick("DOCS_WIKI_LLM_API_KEY", "api_key"),
        chat_model=chat_model,
        reasoning_effort=pick("DOCS_WIKI_REASONING_EFFORT", "reasoning_effort"),
        max_turns=_pick_max_turns(env, section),
    )


def _pick_max_turns(env: Mapping[str, str], section: dict) -> int | None:
    raw = env.get("DOCS_WIKI_MAX_TURNS") or section.get("max_turns")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ConfigError(f"llm.max_turns must be a positive integer, got {raw!r}")
    if value < 1:
        raise ConfigError(f"llm.max_turns must be a positive integer, got {value}")
    return value


@dataclass(frozen=True)
class ServerConfig:
    cors_origin: str | None
    session_retention_days: int
    ui_dist: str | None = None
    state_dir: str | None = None
    admin_group: str = "docs-admins"


def resolve_server(server_section: dict | None,
                   env: Mapping[str, str] | None = None) -> ServerConfig:
    if env is None:
        env = os.environ
    section = server_section or {}
    cors = env.get("DOCS_WIKI_CORS_ORIGIN") or section.get("cors_origin")
    if cors is not None and not isinstance(cors, str):
        raise ConfigError("[server] cors_origin must be a string")
    days = section.get("session_retention_days", 90)
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ConfigError(
            "[server] session_retention_days must be a non-negative integer")
    ui_dist = env.get("DOCS_WIKI_UI_DIST") or section.get("ui_dist")
    if ui_dist is not None and not isinstance(ui_dist, str):
        raise ConfigError("[server] ui_dist must be a string")
    state_dir = env.get("DOCS_WIKI_STATE_DIR") or section.get("state_dir")
    if state_dir is not None and not isinstance(state_dir, str):
        raise ConfigError("[server] state_dir must be a string")
    admin_group = env.get("DOCS_WIKI_ADMIN_GROUP") or section.get("admin_group")
    if admin_group is not None and not isinstance(admin_group, str):
        raise ConfigError("[server] admin_group must be a string")
    return ServerConfig(cors_origin=cors or None,
                        session_retention_days=days,
                        ui_dist=ui_dist or None,
                        state_dir=state_dir or None,
                        admin_group=admin_group or "docs-admins")


def state_root(server: ServerConfig) -> Path:
    """Where gaps/sessions live: `<state_dir>/<app>/...`. Defaults to
    `~/.local/state/docs-wiki` when no state_dir is configured."""
    if server.state_dir:
        return Path(server.state_dir).expanduser()
    return Path.home() / ".local" / "state" / "docs-wiki"


def resolve_rag(rag_section: dict | None,
                env: Mapping[str, str] | None = None) -> str | None:
    if env is None:
        env = os.environ
    section = rag_section or {}
    url = env.get("DOCS_WIKI_RAG_URL") or section.get("url")
    return url if isinstance(url, str) and url else None


def resolve_code_agent(code_agent_section: dict | None,
                       env: Mapping[str, str] | None = None) -> str | None:
    # The code-agent service is always optional (README: "a deploy
    # without [code_agent] configured on docs-wiki behaves exactly as
    # if this service did not exist") — same empty-section shape as
    # resolve_rag above, deliberately mirrored rather than shared, since
    # each config surface is free to diverge independently later.
    if env is None:
        env = os.environ
    section = code_agent_section or {}
    url = env.get("DOCS_WIKI_CODE_AGENT_URL") or section.get("url")
    return url if isinstance(url, str) and url else None


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
