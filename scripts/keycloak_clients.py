"""Apply the two v3 OIDC client definitions without reimporting users or realm data."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from string import Template
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
        if client["clientId"] != "knowledge-agent-v3-admin":
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
    # The Reader application and its login client were removed; delete the client a persisted realm still has.
    endpoint = origin + "/admin/realms/docs/clients"
    for retired in request_json(endpoint + "?" + urllib.parse.urlencode({"clientId": "knowledge-agent-v3"}), token=token):
        request_json(endpoint + "/" + retired["id"], method="DELETE", token=token)
        print("Removed retired OIDC client: knowledge-agent-v3")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit(f"Keycloak client configuration failed: HTTP {error.code}. Check bootstrap credentials.") from None
    except (OSError, ValueError) as error:
        raise SystemExit(f"Keycloak client configuration failed: {error}") from None
