# Repository service

FastAPI provides repository inventory and read-only source browsing for the admin
application. `POST /repos/{name}/ask` retains its request validation and agent
repository scope checks, then returns plain text `TODO` with HTTP 200. It never
calls a model or executes repository code.

Compose mounts code project folders at `/work/code` read-only. `CODE_ROOT` selects
the database-backed inventory; without it, a TOML `[[repos]]` list supplies names
and paths. The database inventory resolves every mapped folder in each selected code project.
Git roots expose tracked files; ordinary folders expose their files recursively.
Returned file paths are relative to CODE_ROOT, so multiple mapped folders remain
distinguishable. Source routes enforce agent project selections, traversal, symlink,
binary, file-size and inventory-size restrictions.

The Codex CLI, SDK runner and model gateway are removed. Model assignments remain
in administration as records. No model environment variables are needed here.

Run tests with `uv run --frozen pytest -q` in this directory.
