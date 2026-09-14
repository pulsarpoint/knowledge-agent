"""rag CLI: explicit indexing, API serving."""
from __future__ import annotations

import os
import argparse
import sys
from pathlib import Path

from rag import ingest
from rag.config import Config, ConfigError, load_config, load_env_file, resolve_embed
from rag.embed_client import EmbedClient


def _run_server(app, host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port)


def _resolve_embed_client(llm_section: dict | None) -> EmbedClient | None:
    embed_cfg = resolve_embed(llm_section)
    return EmbedClient(embed_cfg) if embed_cfg else None


def _bm25_notice() -> None:
    print("embeddings not configured -- retrieval is BM25-only (set "
          "RAG_EMBED_BASE_URL/RAG_EMBED_MODEL or [llm] in the config to enable "
          "hybrid search)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="build/rebuild collection indexes")
    index.add_argument("target", type=Path)
    index.add_argument("--env-file", type=Path, default=None)
    serve = sub.add_parser("serve", help="serve the retrieval API")
    serve.add_argument("target", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8790)
    serve.add_argument("--env-file", type=Path, default=None)
    serve.add_argument("--reindex", action="store_true",
                       help="run one index pass before serving")
    workspace = sub.add_parser("serve-workspace", help="serve the PostgreSQL folder workspace API")
    workspace.add_argument("--host", default="127.0.0.1")
    workspace.add_argument("--port", type=int, default=8790)
    workspace.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)


    try:
        load_env_file(args.env_file)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "serve-workspace":
        from rag.api import create_app
        from rag.workspace import Workspace

        app = create_app(Config(collections=[], llm=None),
                         workspace=Workspace.from_env(),
                         workspace_key=os.environ.get("RAG_BACKOFFICE_KEY", ""))
        _run_server(app, args.host, args.port)
        return 0

    try:
        cfg = load_config(args.target)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    embed = _resolve_embed_client(cfg.llm)

    if args.command == "index" or args.reindex:
        for collection in cfg.collections:
            counts = ingest.index_collection(collection.path, embed=embed,
                                             name=collection.name,
                                             kind=collection.kind,
                                             db_dir=collection.db_dir)
            print(ingest.format_index_counts(collection.name, counts,
                                             embed is not None))
        if args.command == "index":
            return 0

    from rag.api import create_app
    if embed is None:
        _bm25_notice()
    _run_server(create_app(cfg, embed=embed), args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
