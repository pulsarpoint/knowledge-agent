"""Apply the two v3 OIDC client definitions without reimporting users or realm data."""

import json
import os
import urllib.error
import urllib.parse
from pathlib import Path
from string import Template

from scripts.admin_user import request_json


def main() -> None:
    origin = "http://keycloak:8080"
    token = request_json(origin + "/realms/master/protocol/openid-connect/token", method="POST", form={
        "grant_type": "password", "client_id": "admin-cli",
        "username": os.environ["KC_BOOTSTRAP_ADMIN_USERNAME"],
        "password": os.environ["KC_BOOTSTRAP_ADMIN_PASSWORD"],
    })["access_token"]
    template = json.loads(Path("/config/realm-docs.json").read_text(encoding="utf-8"))
    for definition in template["clients"]:
        client = json.loads(Template(json.dumps(definition)).substitute(os.environ))
        if client["clientId"] not in ("knowledge-agent-v3", "knowledge-agent-v3-admin"):
            raise ValueError("Unexpected client in v3 configuration")
        endpoint = origin + "/admin/realms/docs/clients"
        existing = request_json(endpoint + "?" + urllib.parse.urlencode({"clientId": client["clientId"]}), token=token)
        if len(existing) > 1:
            raise ValueError("Ambiguous Keycloak client")
        if existing:
            request_json(endpoint + "/" + existing[0]["id"], method="PUT", token=token, payload=client)
        else:
            request_json(endpoint, method="POST", token=token, payload=client)
        print(f"Configured OIDC client: {client['clientId']}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Keycloak client configuration failed: HTTP {error.code}. Check bootstrap credentials.") from None
    except (OSError, ValueError) as error:
        raise SystemExit(f"Keycloak client configuration failed: {error}") from None
