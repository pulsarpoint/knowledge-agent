"""rag CLI: serve the workspace API, run the sync worker, check the chunk contract."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _run_server(app, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


def load_env_file(path: Path | None) -> None:
    """Load `path` (must exist) or, when omitted, `./.env` if present.
    Variables already set in the environment win."""
    from dotenv import load_dotenv

    if path is not None:
        if not Path(path).is_file():
            raise FileNotFoundError(f'env file does not exist: {path}')
        load_dotenv(path, override=False)
        return
    default = Path.cwd() / '.env'
    if default.is_file():
        load_dotenv(default, override=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='rag')
    sub = parser.add_subparsers(dest='command', required=True)
    workspace = sub.add_parser('serve-workspace', help='serve the PostgreSQL folder workspace API')
    workspace.add_argument('--host', default='127.0.0.1')
    workspace.add_argument('--port', type=int, default=8790)
    workspace.add_argument('--env-file', type=Path, default=None)
    worker = sub.add_parser('worker', help='run the collection sync worker')
    worker.add_argument('--env-file', type=Path, default=None)
    index = sub.add_parser('index', help='check a docs folder against the chunk contract (writes nothing)')
    index.add_argument('target', type=Path)
    args = parser.parse_args(argv)

    if args.command == 'index':
        from rag.chunking import check_folder

        if not args.target.is_dir():
            print(f'error: not a docs folder: {args.target}', file=sys.stderr)
            return 2
        documents, warnings = check_folder(args.target)
        line = f'{args.target.name}: checked {documents}'
        if warnings:
            line += f', chunk-warnings {warnings}'
        print(line)
        return 0

    try:
        load_env_file(args.env_file)
    except FileNotFoundError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    if args.command == 'worker':
        from rag.workspace_worker import main as worker_main

        worker_main()
        return 0

    from rag.api import create_app
    from rag.workspace import Workspace

    app = create_app(workspace=Workspace.from_env(), workspace_key=os.environ.get('RAG_BACKOFFICE_KEY', ''))
    _run_server(app, args.host, args.port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
