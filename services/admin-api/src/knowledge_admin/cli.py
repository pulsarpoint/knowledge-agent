"""Run the standalone administration API."""

import argparse
import base64
import os
import sys
from pathlib import Path

import uvicorn

from knowledge_admin.logs import configure as configure_logging
from knowledge_admin.config import ConfigError, load_config, load_env_file, resolve_server
from knowledge_admin.app import create_app
from knowledge_admin.repositories import RepositoryStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, help='config.toml or docs directory')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    try:
        load_env_file(args.env_file)
        cfg = load_config(args.target)
        server = resolve_server(cfg.server)
        log_level = configure_logging(os.environ.get('LOG_LEVEL'))  # after the env file, which may set it
    except (ConfigError, ValueError) as exc:  # ValueError: an invalid LOG_LEVEL
        print(f'error: {exc}', file=sys.stderr)
        return 2
    repository_store = None
    if os.environ.get('CREDENTIAL_ENCRYPTION_KEY'):
        repository_store = RepositoryStore(
            '', base64.urlsafe_b64decode(os.environ['CREDENTIAL_ENCRYPTION_KEY']), os.environ['AUTH_ISSUER_URL']
        )
    uvicorn.run(
        create_app(cfg, server=server, repository_store=repository_store),
        host=args.host,
        port=args.port,
        timeout_graceful_shutdown=5,
        log_level=log_level,
    )  # open session streams reconnect; they must not block shutdown
    return 0
