"""docs-wiki CLI: serve the API + UI over the RAG service's collections.

Indexing lives entirely in the sibling `rag` service (collections are synced
from Administration → RAG) — this CLI never touches a local index.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from docs_wiki.config import (ConfigError, load_config, load_env_file,
                              resolve_rag, resolve_server)
from docs_wiki.webapp import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docs-wiki",
        description="Serve documentation over HTTP from the rag service's "
                    "collections (DOCS_WIKI_RAG_URL and RAG_BACKOFFICE_KEY).")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the API + UI")
    serve.add_argument("target", type=Path, help="config.toml or a docs folder")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        load_env_file(args.env_file)
        cfg = load_config(args.target)
        server_cfg = resolve_server(cfg.server)
        rag_url = resolve_rag(cfg.rag)
        if not rag_url:
            raise ConfigError(
                "docs-wiki serve requires a RAG service URL: set [rag] url "
                "in the config or the DOCS_WIKI_RAG_URL environment variable")
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    uvicorn.run(create_app(cfg, server=server_cfg),
                host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
