"""Prepare independent local configuration without importing v2 secrets or data."""

import base64
import os
import re
import secrets
import shutil
from pathlib import Path


# Folders inside DATA_ROOT, by .env variable, with their defaults. Compose mounts DATA_ROOT once at
# /work/data and computes each service's root from these (CODE_ROOT=/work/data/${CODE_FOLDER}, ...).
FOLDERS = {
    "CODE_FOLDER": "code",
    "CODE_INDEX_FOLDER": "code-index",
    "DOCS_FOLDER": "docs",
    "GENERATED_DOCS_FOLDER": "generated-documents",
    "JIRA_FOLDER": "jira",
}
# Absolute overrides from before the one data folder, and the variable that replaces each.
REMOVED = {
    "CODE_PATH": "CODE_FOLDER",
    "CODE_INDEX_PATH": "CODE_INDEX_FOLDER",
    "RG_DATA": "JIRA_FOLDER",
    "GENERATED_DOCS_PATH": "GENERATED_DOCS_FOLDER",
    "DOCS_PATH": "DOCS_FOLDER",
    "RAG_SOURCE_ROOT": None,
}
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")  # the same rule as a RAG source_path segment
OLD_JIRA = Path("rag-data") / "jira"
USAGE = "make setup DATA_ROOT=/opt/knowledge-agent"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].replace("\\'", "'")
    return value


def _quote(path: Path) -> str:
    # Single quotes keep spaces, dollar signs, and # literal in Compose's .env.
    return "'" + str(path).replace("'", "\\'") + "'"


def _absolute(name: str, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SystemExit(f"{name} must be an absolute host path, got {value!r}. Example: {USAGE}")
    return path


def _refuse_removed(data: Path, current: dict[str, str]) -> None:
    """Stop when an absolute override from before the one data folder is still set."""
    for key, replacement in REMOVED.items():
        value = _unquote(current.get(key, ""))
        if not value:
            continue
        if replacement is None:
            raise SystemExit(f"{key} is no longer used; remove it from .env.")
        if key == "RG_DATA":
            raise SystemExit("RG_DATA is no longer used; remove it from .env. Jira exports now go to JIRA_FOLDER inside "
                             f"DATA_ROOT (default jira); make setup moves {data / OLD_JIRA} there.")
        path = Path(value).expanduser()
        if path.is_absolute() and path != data and path.is_relative_to(data):
            raise SystemExit(f"{key} is no longer used. Replace it in .env with {replacement}={path.relative_to(data).as_posix()}.")
        raise SystemExit(f"{key} is no longer used. Move {value} into {data}, then replace {key} in .env with "
                         f"{replacement}=<folder inside DATA_ROOT>.")


def _folders(current: dict[str, str], defaults: dict[str, str]) -> dict[str, str]:
    """The five folder variables as validated relative paths; overlap is refused."""
    folders = {}
    for key, default in FOLDERS.items():
        value = _unquote(current.get(key, "")) or _unquote(defaults.get(key, "")) or default
        if not all(SEGMENT.fullmatch(part) for part in value.split("/")):
            raise SystemExit(f"{key} must be a folder inside DATA_ROOT such as {default}: a relative path without a "
                             f"leading /, .., hidden or empty segments; got {value!r}.")
        folders[key] = value
    keys = list(folders)
    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            a, b = folders[first], folders[second]
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                raise SystemExit(f"{first} ({a}) and {second} ({b}) overlap; choose separate folders inside DATA_ROOT.")
    return folders


def _create(data: Path, folders: list[Path]) -> None:
    for folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise SystemExit(
                f"Cannot create {folder}. Create the data folder once with:\n"
                f"  sudo mkdir -p {data} && sudo chown $(id -u):$(id -g) {data}"
            ) from None


def _move_jira(data: Path, jira: str) -> None:
    """Move DATA_ROOT/rag-data/jira to the Jira folder once.

    An empty Jira folder is replaced; anything else there stops setup, because migration 0067 points
    the Jira collection at the Jira folder and the next sync would drop what stayed in rag-data/jira.
    Files are never merged or deleted.
    """
    old, new = data / OLD_JIRA, data / jira
    if not old.is_dir() or old.is_symlink():
        return
    if new.exists() or new.is_symlink():
        if new.is_symlink() or not new.is_dir() or any(new.iterdir()):
            raise SystemExit(f"Both {old} and {new} exist. Merge them by hand: move the files from {old} "
                             f"into {new} and remove {old}, then run make setup again. Nothing was moved.")
        new.rmdir()
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    print(f"Moved {old} to {new}.")
    try:
        old.parent.rmdir()  # only succeeds when the move left rag-data empty
    except OSError:
        return
    print(f"Removed the empty folder {old.parent}.")


def _warn_other_entries(data: Path, folders: dict[str, str]) -> None:
    """Name every top-level entry that is not a configured folder; nothing is moved."""
    configured = {folder.split("/")[0] for folder in folders.values()}
    others = sorted(entry.name for entry in data.iterdir() if entry.name not in configured and entry.name != ".DS_Store")
    if others:
        print(f"Warning: {data} holds entries that are not configured folders: {', '.join(others)}. "
              "RAG can see every folder in DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE folder. "
              "Nothing was moved.")


def initialize(root: Path, data_root: str | None = None) -> None:
    env_path = root / ".env"
    template = (root / ".env.example").read_text(encoding="utf-8")
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else template
    defaults = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", template, re.MULTILINE))
    current = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))
    data_root = data_root or _unquote(current.get("DATA_ROOT", ""))
    if not data_root:
        raise SystemExit(f"Choose where the application keeps its files: {USAGE}")
    data = _absolute("DATA_ROOT", data_root)
    _refuse_removed(data, current)
    folders = _folders(current, defaults)
    _create(data, [data])
    _move_jira(data, folders["JIRA_FOLDER"])
    _create(data, [data / folder for folder in folders.values()])
    defaults["DATA_ROOT"] = _quote(data)
    secret_keys = {
        "POSTGRES_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "APP_ADMIN_PASSWORD",
        "OAUTH2_PROXY_CLIENT_SECRET", "OAUTH2_PROXY_COOKIE_SECRET", "PHOENIX_SECRET",
        "ADMIN_OAUTH2_PROXY_CLIENT_SECRET", "ADMIN_OAUTH2_PROXY_COOKIE_SECRET",
        "PHOENIX_ADMIN_PASSWORD", "PHOENIX_ADMIN_SECRET", "LITELLM_MASTER_KEY",
        "CREDENTIAL_ENCRYPTION_KEY", "RAG_BACKOFFICE_KEY", "READER_AGENT_KEY",
    }
    for key, default in defaults.items():
        existing = current.get(key, "").strip().strip("\"'")
        if key == "DATA_ROOT":
            if current.get(key) == default:
                continue
        elif key in current and (existing or (key not in secret_keys and key not in FOLDERS)):
            continue
        value = default
        if key in secret_keys:
            value = secrets.token_urlsafe(32)
            if key.endswith("OAUTH2_PROXY_COOKIE_SECRET") or key == "CREDENTIAL_ENCRYPTION_KEY":
                value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
            elif key == "LITELLM_MASTER_KEY":
                value = "sk-" + value
        if key in current:
            text = re.sub(rf"^{key}=.*$", lambda _: f"{key}={value}", text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + f"\n{key}={value}\n"

    # The temporary file is private from creation; atomic replacement never
    # leaves a partially written secrets file if setup is interrupted.
    temporary = root / f".env.setup-{secrets.token_hex(8)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
    temporary.replace(env_path)

    docs_path = data / folders["DOCS_FOLDER"]
    if not any(docs_path.iterdir()):
        shutil.copytree(root / "examples" / "docs", docs_path, dirs_exist_ok=True)
    _warn_other_entries(data, folders)
    print(f"Local configuration ready. Credentials are in .env (mode 0600). Files are kept in {data}.")
    print("Run make up, then make smoke to verify login and services. Load and process content through RAG → Collections.")


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1], os.environ.get("DATA_ROOT") or None)
