"""Prepare independent local configuration without importing v2 secrets or data."""

import base64
import os
import re
import secrets
import shutil
from pathlib import Path


def initialize(root: Path) -> None:
    env_path = root / ".env"
    template = (root / ".env.example").read_text(encoding="utf-8")
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else template
    defaults = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", template, re.MULTILINE))
    current = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.MULTILINE))
    secret_keys = {
        "POSTGRES_PASSWORD", "KEYCLOAK_ADMIN_PASSWORD", "APP_ADMIN_PASSWORD",
        "OAUTH2_PROXY_CLIENT_SECRET", "OAUTH2_PROXY_COOKIE_SECRET", "PHOENIX_SECRET",
        "ADMIN_OAUTH2_PROXY_CLIENT_SECRET", "ADMIN_OAUTH2_PROXY_COOKIE_SECRET",
        "PHOENIX_ADMIN_PASSWORD", "PHOENIX_ADMIN_SECRET", "LITELLM_MASTER_KEY",
        "CREDENTIAL_ENCRYPTION_KEY", "RAG_BACKOFFICE_KEY", "READER_AGENT_KEY",
    }
    for key, default in defaults.items():
        existing = current.get(key, "").strip().strip("\"'")
        if key in current and (existing or key not in secret_keys | {"RG_DATA"}):
            continue
        value = default
        if key == "RG_DATA":
            # Single quotes keep spaces, dollar signs, and # literal in Compose's .env.
            path = str(root.resolve() / ".local" / "rag-data").replace("'", "\\'")
            value = f"'{path}'"
        elif key in secret_keys:
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

    (root / ".local" / "rag-data").mkdir(parents=True, exist_ok=True)
    docs_path = root / ".local" / "docs"
    docs_path.mkdir(parents=True, exist_ok=True)
    (root / ".local" / "code").mkdir(parents=True, exist_ok=True)
    if not any(docs_path.iterdir()):
        shutil.copytree(root / "examples" / "docs", docs_path, dirs_exist_ok=True)
    print("Local configuration ready. Credentials are in .env (mode 0600).")
    print("Run make up, then make smoke to verify login and services. Load and process content through RAG → Files & folders.")


if __name__ == "__main__":
    initialize(Path(__file__).resolve().parents[1])
