"""Run the standalone administration API."""

import argparse
import base64
import os
import sys
from pathlib import Path

import httpx
import uvicorn

from knowledge_admin.config import ConfigError, load_config, load_env_file, resolve_rag, resolve_server
from knowledge_admin.app import create_app
from knowledge_admin.repositories import RepositoryStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="config.toml or docs directory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        load_env_file(args.env_file)
        cfg = load_config(args.target)
        server = resolve_server(cfg.server)
        rag_url = resolve_rag(cfg.rag)
        if not rag_url:
            raise ConfigError("Set DOCS_WIKI_RAG_URL or [rag] url before starting the admin API")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    repository_store = None
    if os.environ.get("CREDENTIAL_ENCRYPTION_KEY"):
        repository_store = RepositoryStore("", base64.urlsafe_b64decode(os.environ["CREDENTIAL_ENCRYPTION_KEY"]),
                                           os.environ["AUTH_ISSUER_URL"])
    with httpx.Client(base_url=rag_url, timeout=30.0) as backoffice:
        uvicorn.run(create_app(cfg, server=server, backoffice_client=backoffice, repository_store=repository_store),
                    host=args.host, port=args.port)
    return 0
