"""code-agent CLI: serve the code-question API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from code_agent.config import (ConfigError, load_config, load_env_file)


def _run_server(app, host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="code-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="serve the code-question API")
    serve.add_argument("target", type=Path)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8795)
    serve.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        load_env_file(args.env_file)
        cfg = load_config(args.target)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from code_agent.api import create_app
    _run_server(create_app(cfg), args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
