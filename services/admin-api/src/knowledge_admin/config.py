"""Startup configuration: the workspace (a docs folder or a TOML file) and the admin group."""

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


@dataclass
class Config:
    apps: list[App]
    server: dict | None = None


@dataclass(frozen=True)
class ServerConfig:
    admin_group: str = 'docs-admins'


def load_config(target: Path) -> Config:
    """A docs folder names the single workspace; a TOML file lists [[apps]] with an optional [server] table."""
    target = Path(target)
    if target.is_dir():
        return Config(apps=[App(name=target.name)])
    if not target.is_file():
        raise ConfigError(f'config target does not exist: {target}')
    try:
        data = tomllib.loads(target.read_text(encoding='utf-8'))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'invalid TOML in {target}: {exc}') from exc
    raw_apps = data.get('apps') or []
    if not isinstance(raw_apps, list) or not raw_apps:
        raise ConfigError(f'{target}: add at least one [[apps]] entry')
    apps: list[App] = []
    for i, entry in enumerate(raw_apps):
        name = entry.get('name') if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name:
            raise ConfigError(f'{target}: apps entry {i} must have a string name')
        if name in {app.name for app in apps}:
            raise ConfigError(f'{target}: duplicate app name {name!r}')
        apps.append(App(name=name))
    server = data.get('server')
    if server is not None and not isinstance(server, dict):
        raise ConfigError(f'{target}: server must be a table')
    return Config(apps=apps, server=server)


def resolve_server(server_section: dict | None, env: Mapping[str, str] | None = None) -> ServerConfig:
    env = os.environ if env is None else env
    admin_group = env.get('DOCS_WIKI_ADMIN_GROUP') or (server_section or {}).get('admin_group')
    if admin_group is not None and not isinstance(admin_group, str):
        raise ConfigError('[server] admin_group must be a string')
    return ServerConfig(admin_group=admin_group or 'docs-admins')


def load_env_file(path: Path | None) -> None:
    from dotenv import load_dotenv

    if path is not None:
        if not Path(path).is_file():
            raise ConfigError(f'env file does not exist: {path}')
        load_dotenv(path, override=False)
        return
    default = Path.cwd() / '.env'
    if default.is_file():
        load_dotenv(default, override=False)
