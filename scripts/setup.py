"""Prepare independent local configuration without importing v2 secrets or data."""

import base64
import os
import re
import secrets
import shutil
from pathlib import Path


# Host folders mounted into the containers: optional override variable and its
# default location under DATA_ROOT. Compose applies the same defaults.
DATA_FOLDERS = {
    "CODE_PATH": "code",
    "CODE_INDEX_PATH": "code-index",
    "RG_DATA": "rag-data",
    "GENERATED_DOCS_PATH": "generated-documents",
    "DOCS_PATH": "docs",
}
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


def _data_folders(data_root: Path, current: dict[str, str]) -> list[Path]:
    folders = []
    for key, name in DATA_FOLDERS.items():
        override = _unquote(current.get(key, ""))
        folders.append(_absolute(key, override) if override else data_root / name)
    for folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            raise SystemExit(
                f"Cannot create {folder}. Create the data folder once with:\n"
                f"  sudo mkdir -p {data_root} && sudo chown $(id -u):$(id -g) {data_root}"
            ) from None
    return folders


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
    folders = _data_folders(data, current)
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
        elif key in current and (existing or key not in secret_keys):
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

    docs_path = folders[list(DATA_FOLDERS).index("DOCS_PATH")]
    if not any(docs_path.iterdir()):
        shutil.copytree(root / "examples" / "docs", docs_path, dirs_exist_ok=True)
    print(f"Local configuration ready. Credentials are in .env (mode 0600). Files are kept in {data}.")
    print("Run make up, then make smoke to verify login and services. Load and process content through RAG → Files & folders.")


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1], os.environ.get("DATA_ROOT") or None)
