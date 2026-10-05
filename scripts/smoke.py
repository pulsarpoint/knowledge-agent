"""Verify local SSO, the current RAG workspace, and services without processing data."""

import argparse
import http.cookiejar
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path


class LoginForm(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "form" and attributes.get("id") == "kc-form-login":
            self.action = attributes.get("action") or ""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LocalhostCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    def return_ok_secure(self, cookie, request):
        # Browsers allow Secure cookies on HTTP localhost. Python's CookieJar
        # does not, so mirror that exception only for loopback smoke checks.
        url = urllib.parse.urlsplit(request.full_url)
        if url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1"):
            return True
        return super().return_ok_secure(cookie, request)


def login(origin: str, keycloak_origin: str, credentials: dict[str, str], title: str):
    cookies = http.cookiejar.CookieJar(policy=LocalhostCookiePolicy())
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    with browser.open(origin + "/", timeout=15) as response:
        form = LoginForm()
        form.feed(response.read().decode("utf-8"))
    if not form.action:
        raise RuntimeError("Keycloak did not return its login form")
    # Never submit credentials to an unexpected login destination.
    expected = urllib.parse.urlsplit(keycloak_origin)
    actual = urllib.parse.urlsplit(form.action)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        raise RuntimeError("Keycloak login form points to an unexpected host")
    payload = urllib.parse.urlencode({
        "username": credentials["username"],
        "password": credentials["password"],
        "credentialId": "",
    }).encode("utf-8")
    with browser.open(urllib.request.Request(form.action, data=payload), timeout=20) as response:
        page = response.read().decode("utf-8")
    if f"<title>{title}</title>" not in page:
        raise RuntimeError("SSO login did not reach the v3 application")
    return browser, cookies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, help="JSON username/password file from make admin-user")
    parser.add_argument("--auth-only", action="store_true", help="verify admin login and access only")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    compose = ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(root / "deploy/compose.yaml")]
    # Compose resolves dotenv and defaults. Keep the result in memory: it contains secrets.
    config = json.loads(subprocess.run(
        [*compose, "config", "--format", "json"], check=True, capture_output=True, text=True,
    ).stdout)
    services = config["services"]
    for service in services.values():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind" and not Path(mount["source"]).resolve().is_relative_to(root):
                print("Using an explicitly configured external content directory.")
    if "knowledge-agent-v2" in json.dumps(config):
        raise RuntimeError("The runtime configuration still references v2")
    print("PASS: runtime configuration has no v2 dependency")

    auth = services["oauth2-proxy"]["environment"]
    origin = auth["OAUTH2_PROXY_REDIRECT_URL"].removesuffix("/oauth2/callback")
    request = urllib.request.Request(origin + "/api/apps", headers={"X-Forwarded-Groups": "docs-admins"})
    try:
        urllib.request.build_opener(NoRedirect()).open(request, timeout=10)
    except urllib.error.HTTPError as error:
        if error.code not in (302, 401, 403):
            raise RuntimeError(f"Unexpected unauthenticated HTTP status: {error.code}") from None
    else:
        raise RuntimeError("Unauthenticated API access was not rejected")
    print("PASS: gateway rejects unauthenticated requests and spoofed admin headers")

    environment = services["keycloak"]["environment"]
    credentials = json.loads(args.credentials.read_text(encoding="utf-8")) if args.credentials is not None else {
        "username": environment["APP_ADMIN_USERNAME"], "password": environment["APP_ADMIN_PASSWORD"],
    }
    browser, reader_cookies = login(origin, environment["KC_HOSTNAME"], credentials, "Knowledge Agent v3")
    with browser.open(origin + "/api/apps", timeout=10) as response:
        apps = json.load(response)
    if not apps or "is_admin" in apps[0]:
        raise RuntimeError("Reader discovery is empty or still exposes admin capabilities")
    admin_origin = services["admin-oauth2-proxy"]["environment"]["OAUTH2_PROXY_REDIRECT_URL"].removesuffix("/oauth2/callback")
    # A reader session must not authenticate to the admin application.
    isolated = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(reader_cookies))
    try:
        isolated.open(admin_origin + "/api/session", timeout=10)
    except urllib.error.HTTPError as error:
        if error.code not in (302, 401, 403):
            raise RuntimeError(f"Unexpected admin gateway status: {error.code}") from None
    else:
        raise RuntimeError("The admin gateway accepted a reader session")
    admin, _ = login(admin_origin, environment["KC_HOSTNAME"], credentials, "Knowledge Agent Administration")
    with admin.open(admin_origin + "/api/session", timeout=10) as response:
        admin_session = json.load(response)
    if not admin_session.get("is_admin"):
        raise RuntimeError("Admin group did not reach the standalone admin API")
    with admin.open(admin_origin + "/api/rag/collections", timeout=10) as response:
        json.load(response)
    app_name = urllib.parse.quote(apps[0]["name"], safe="")
    for path in ("/api/rag/collections", f"/api/{app_name}/admin/audit", f"/{app_name}/admin"):
        try:
            browser.open(origin + path, timeout=10)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise RuntimeError(f"Reader unexpectedly returned HTTP {error.code} for {path}") from None
        else:
            raise RuntimeError(f"Reader still exposes administration: {path}")
    try:
        admin.open(admin_origin + f"/api/{app_name}/toc", timeout=10)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    else:
        raise RuntimeError("Admin API exposes reader endpoints")
    print("PASS: independent reader/admin logins, isolated sessions, disjoint APIs and removed reader admin pages")
    # The internal Reader API is not routed by the admin gateway (nginx only proxies /api/).
    with admin.open(admin_origin + "/internal/reader/sessions", timeout=10) as response:
        if "application/json" in response.headers.get("Content-Type", ""):
            raise RuntimeError("The admin gateway exposes the internal Reader API")
    rejected = subprocess.run(
        [*compose, "exec", "-T", "docs-wiki", "/app/.venv/bin/python", "-c",
         "import urllib.request,urllib.error\n"
         "try:\n urllib.request.urlopen(urllib.request.Request('http://admin-api:8766/internal/reader/sessions',"
         "headers={'X-Reader-User':'smoke'}),timeout=5)\nexcept urllib.error.HTTPError as e:\n print(e.code)"],
        check=True, capture_output=True, text=True).stdout.strip()
    if rejected != "401":
        raise RuntimeError(f"Internal Reader API accepted a request without its key (HTTP {rejected or 'success'})")
    print("PASS: internal Reader API requires its key and is not exposed by the admin gateway")
    if args.auth_only:
        return

    for resource, expected in (("settings", "settings"), ("collections", "collections"), ("jobs", "jobs")):
        with admin.open(admin_origin + "/api/rag/" + resource, timeout=10) as response:
            payload = json.load(response)
        if expected not in payload:
            raise RuntimeError(f"RAG {resource} returned an unexpected response")
    with browser.open(origin + f"/api/{app_name}/toc", timeout=10) as response:
        toc = json.load(response)
    documents = [doc for group in toc for doc in group["docs"]]
    if documents:
        doc_path = documents[0]["path"]
        with browser.open(origin + f"/api/{app_name}/doc?" + urllib.parse.urlencode({"path": doc_path}), timeout=10) as response:
            document = json.load(response)
        if not document["markdown"]:
            raise RuntimeError("Indexed Markdown could not be read")
        print("PASS: Markdown retrieval and source catalog")
    else:
        print("PASS: source catalog available; no processed documents yet")
    # Full-text mode verifies retrieval without calling an embedding model.
    payload = json.dumps({"query": "documentation", "top": 1}).encode()
    request = urllib.request.Request(admin_origin + "/api/rag/search", data=payload,
                                     headers={"Content-Type": "application/json"})
    with admin.open(request, timeout=10) as response:
        hits = json.load(response)
    if not isinstance(hits.get("results"), list) or "collections" not in hits or "fallback" not in hits:
        raise RuntimeError("RAG full-text search returned an unexpected response")
    print("PASS: RAG settings, collections, processing history, and full-text search")

    migration_state = subprocess.run(
        [*compose, "exec", "-T", "postgres", "psql", "-U", "knowledge_agent", "-d", "knowledge_agent",
         "-Atc", "SELECT version,dirty FROM public.golang_schema_migrations"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    expected_version = max(int(p.name.split('_', 1)[0]) for p in (root / 'db/migrations').glob('*.up.sql'))
    if migration_state != f"{expected_version}|f":
        raise RuntimeError("PostgreSQL migrations are missing, outdated, or dirty")
    print("PASS: golang-migrate records the current clean PostgreSQL version")
    phoenix_port = services["phoenix"]["ports"][0]["published"]
    with urllib.request.urlopen(f"http://127.0.0.1:{phoenix_port}/", timeout=10):
        pass
    print("PASS: independent Phoenix instance responds")
    print("No model calls were made. Agent execution returns TODO; RAG embeddings use configured models.")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        feedback = re.search(r'class="kc-feedback-text">([^<]+)', body)
        detail = f": {feedback.group(1)}" if feedback else ""
        raise SystemExit(f"Smoke test failed: HTTP {error.code} at {urllib.parse.urlsplit(error.url).path}{detail}") from None
    except (RuntimeError, urllib.error.URLError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Smoke test failed: {error}") from None
