"""Create an application administrator in the running local v3 Keycloak realm."""

import argparse
import json
import os
import re
import secrets
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(
    url: str, *, method: str = "GET", token: str = "", payload: Any = None,
    form: dict[str, str] | None = None,
) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # Local administration never sends credentials through a proxy or redirect.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener.open(request, timeout=15) as response:
        body = response.read()
        return json.loads(body) if body else None


def ensure_admin(
    root: Path, config: dict[str, Any], username: str, email: str, *, reset_password: bool = False,
) -> Path | None:
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", username) is None:
        raise ValueError("Use 1–64 lowercase letters, numbers, dots, underscores or hyphens; start with a letter or number.")
    if config["name"] != "knowledge-agent-v3":
        raise ValueError("This command only manages the knowledge-agent-v3 Compose project.")
    environment = config["services"]["keycloak"]["environment"]
    origin = environment["KC_HOSTNAME"].rstrip("/")
    url = urllib.parse.urlsplit(origin)
    if url.scheme not in ("http", "https") or url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Local admin creation requires a loopback Keycloak hostname.")
    if url.username is not None or url.password is not None or url.path or url.query or url.fragment:
        raise ValueError("Expected a plain local Keycloak origin.")
    session = request_json(origin + "/realms/master/protocol/openid-connect/token", method="POST", form={
        "grant_type": "password", "client_id": "admin-cli",
        "username": environment["KC_BOOTSTRAP_ADMIN_USERNAME"],
        "password": environment["KC_BOOTSTRAP_ADMIN_PASSWORD"],
    })
    token = session["access_token"]
    realm = origin + "/admin/realms/docs"
    groups = request_json(realm + "/groups?search=docs-admins&exact=true", token=token)
    admins = [group for group in groups if group["path"] == "/docs-admins"]
    if len(admins) != 1:
        raise RuntimeError("The docs-admins group is missing or ambiguous; check the v3 realm import.")
    query = urllib.parse.urlencode({"username": username, "exact": "true"})
    users = request_json(realm + "/users?" + query, token=token)
    if len(users) > 1 or (users and users[0]["username"] != username):
        raise RuntimeError("Keycloak did not return an unambiguous exact username match.")
    if users and not users[0]["enabled"]:
        raise RuntimeError("This account is disabled. Enable it in Keycloak before granting admin access.")

    credentials_path = root / ".local" / "users" / f"{username}.json"
    if not users or reset_password:
        password = secrets.token_urlsafe(24)
        credentials_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = credentials_path.with_suffix(f".pending-{secrets.token_hex(8)}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"username": username, "password": password}, stream, indent=2)
            stream.write("\n")
        credential = {"type": "password", "value": password, "temporary": False}
        # Save a private recovery copy before sending a password-changing request.
        # Keep it on failure, since a lost response can follow a successful write.
        print(f"Generated credentials: {temporary}")
        if users:
            user_id = users[0]["id"]
            request_json(realm + f"/users/{user_id}/reset-password", method="PUT", token=token, payload=credential)
        else:
            request_json(realm + "/users", method="POST", token=token, payload={
                "username": username, "email": email, "emailVerified": True, "enabled": True,
                "firstName": "Local", "lastName": "Administrator", "requiredActions": [],
                "credentials": [credential],
            })
        temporary.replace(credentials_path)
        users = request_json(realm + "/users?" + query, token=token)
        if len(users) != 1 or users[0]["username"] != username:
            raise RuntimeError("Could not find the account after saving its credentials.")
        print("Password reset." if reset_password else "Local account created.")
    else:
        print("Account already exists; password and profile preserved.")

    user_id = users[0]["id"]
    group_id = admins[0]["id"]
    request_json(realm + f"/users/{user_id}/groups/{group_id}", method="PUT", token=token)
    membership = request_json(realm + f"/users/{user_id}/groups?search=docs-admins", token=token)
    if not any(group["id"] == group_id for group in membership):
        raise RuntimeError("Keycloak did not retain the admin group membership.")
    print(f"Application administrator ready: {username}")
    if credentials_path.is_file():
        print(f"Credentials file (mode 0600): {credentials_path}")
        return credentials_path
    print("Use the account's existing password, or pass --reset-password to generate a new one.")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME") or "local-admin")
    parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL") or "")
    parser.add_argument("--reset-password", action="store_true", help="explicitly replace an existing account's password")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compose = ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(root / "deploy/compose.yaml")]
    # Resolve defaults in memory; the complete Compose configuration contains secrets.
    config = json.loads(subprocess.run(
        [*compose, "config", "--format", "json"], check=True, capture_output=True, text=True,
    ).stdout)
    ensure_admin(root, config, args.username, args.email or f"{args.username}@example.test", reset_password=args.reset_password)
    origin = config["services"]["admin-oauth2-proxy"]["environment"]["OAUTH2_PROXY_REDIRECT_URL"].removesuffix("/oauth2/callback")
    print(f"Admin application: {origin}/")
    print("Sign out and sign in again if this account already has an application session.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Admin setup failed: Keycloak HTTP {error.code}. Check realm settings and the current admin credential in .env.") from None
    except urllib.error.URLError:
        raise SystemExit("Cannot reach local Keycloak. Start the environment with make up.") from None
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Admin setup failed: {error}") from None
