"""Deployment contracts for the independently runnable v3 bundle."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
ROOT = "${DATA_ROOT:?Run make setup DATA_ROOT=/path}"


DATA = {"type": "bind", "source": ROOT, "target": "/work/data", "read_only": True}
FOLDERS = {"CODE_FOLDER": "code", "CODE_INDEX_FOLDER": "code-index", "DOCS_FOLDER": "docs",
           "GENERATED_DOCS_FOLDER": "generated-documents", "JIRA_FOLDER": "jira"}
WRITERS = {"rag": ["JIRA_FOLDER"], "rag-worker": [], "shell-worker": [],
           "admin-api": ["CODE_FOLDER", "CODE_INDEX_FOLDER", "GENERATED_DOCS_FOLDER"],
           "repository-worker": ["CODE_FOLDER", "CODE_INDEX_FOLDER"],
           "documentation-worker": ["CODE_INDEX_FOLDER", "GENERATED_DOCS_FOLDER"]}


def folder(variable: str) -> str:
    return "${" + variable + ":-" + FOLDERS[variable] + "}"


def writable(variable: str) -> dict:
    """A writable folder of DATA_ROOT, bound over the read-only base at the same relative path."""
    return {"type": "bind", "source": ROOT + "/" + folder(variable), "target": "/work/data/" + folder(variable)}


def data_mounts(service: dict) -> list:
    return [v for v in service.get("volumes", []) if isinstance(v, dict) and v["target"].startswith("/work/data")]


def test_bundle_keeps_runtime_and_migrates_v3_database():
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["name"] == "knowledge-agent-v3"
    services = compose["services"]
    assert set(services) == {"postgres", "migrate", "rag", "docs-wiki", "web", "keycloak", "oauth2-proxy", "phoenix", "litellm", "admin-api", "admin-web", "admin-oauth2-proxy", "keycloak-configure", "repository-worker", "jira-mock", "rag-worker", "documentation-worker", "shell-worker"}
    assert services["docs-wiki"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["migrate"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    for service in services.values():
        if "build" in service and service is not services["jira-mock"]:
            assert service["build"]["context"] == ".."
        assert "env_file" not in service  # Each service receives only its own settings/secrets.
    assert "knowledge-agent-v2" not in json.dumps(compose)


def test_private_services_and_separate_persistent_state():
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
    for name in ("rag", "docs-wiki", "web", "litellm", "migrate", "admin-api", "admin-web", "keycloak-configure", "repository-worker"):
        assert not compose["services"][name].get("ports")
    for name in ("postgres", "oauth2-proxy", "admin-oauth2-proxy", "keycloak", "phoenix"):
        assert all(port.startswith("127.0.0.1:") for port in compose["services"][name]["ports"])
    assert set(compose["volumes"]) == {"postgres-data", "wiki-data", "keycloak-data", "phoenix-data", "agent-workspaces"}
    assert all(not value or not value.get("external") for value in compose["volumes"].values())


def test_repository_worker_has_its_write_mounts_and_credentials():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    worker = services["repository-worker"]
    assert worker["volumes"] == [DATA, writable("CODE_FOLDER"), writable("CODE_INDEX_FOLDER")]
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for name in ("admin-api", "repository-worker"):
        assert "CREDENTIAL_ENCRYPTION_KEY" in services[name]["environment"]
    for name in ("admin-web", "web"):
        assert "CREDENTIAL_ENCRYPTION_KEY" not in services[name].get("environment", {})


def test_oidc_keeps_issuer_validation_with_internal_token_urls():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))["services"]
    env = services["oauth2-proxy"]["environment"]
    assert env["OAUTH2_PROXY_CLIENT_ID"] == "knowledge-agent-v3"
    assert env["OAUTH2_PROXY_SKIP_OIDC_DISCOVERY"] == "true"
    assert env["OAUTH2_PROXY_REDEEM_URL"].startswith("http://keycloak:8080/")
    assert env["OAUTH2_PROXY_OIDC_JWKS_URL"].startswith("http://keycloak:8080/")
    assert "OAUTH2_PROXY_INSECURE_OIDC_SKIP_ISSUER_VERIFICATION" not in env
    assert env["OAUTH2_PROXY_COOKIE_NAME"] == "_knowledge_agent_v3"
    realm = json.loads((DEPLOY / "keycloak/realm-docs.json").read_text(encoding="utf-8"))
    assert realm["clients"][0]["clientId"] == env["OAUTH2_PROXY_CLIENT_ID"]
    assert realm["users"][0]["groups"] == ["/docs-admins"]


def test_nginx_preserves_streams_without_exposing_internal_rag():
    conf = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    assert "proxy_buffering off" in conf
    assert "resolver 127.0.0.11" in conf
    assert "proxy_read_timeout 300s" in conf
    assert "rag-backoffice" not in conf


def test_administration_has_a_separate_client_session_and_upstream():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))["services"]
    reader = services["oauth2-proxy"]["environment"]
    admin = services["admin-oauth2-proxy"]["environment"]
    assert admin["OAUTH2_PROXY_ALLOWED_GROUPS"] == "docs-admins"
    assert admin["OAUTH2_PROXY_SCOPE"] == "openid email profile"
    for key in ("CLIENT_ID", "CLIENT_SECRET", "COOKIE_NAME", "COOKIE_SECRET", "REDIRECT_URL", "UPSTREAMS"):
        assert reader[f"OAUTH2_PROXY_{key}"] != admin[f"OAUTH2_PROXY_{key}"]
    assert admin["OAUTH2_PROXY_UPSTREAMS"] == "http://admin-web:80"
    assert "admin-api:8766" in (DEPLOY / "nginx-admin.conf").read_text()
    assert "docs-wiki:8765" not in (DEPLOY / "nginx-admin.conf").read_text()


def test_images_use_local_source_and_frozen_dependencies():
    for name in ("rag", "docs-wiki"):
        text = (DEPLOY / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "uv sync --frozen --no-dev" in text
        assert f"COPY services/{name}/src" in text
        assert "knowledge-agent-v2" not in text
    assert "pnpm install --frozen-lockfile" in (DEPLOY / "Dockerfile.web").read_text(encoding="utf-8")
    assert not (DEPLOY / "Dockerfile.code-agent").exists()


def test_agents_use_database_models_and_keep_keys_out_of_web_containers():
    services = yaml.safe_load((DEPLOY / 'compose.yaml').read_text())['services']
    for name in ('docs-wiki', 'admin-api'):
        env = services[name]['environment']
        assert env['LLM_SETTINGS_SOURCE'] == 'database'
        assert 'CREDENTIAL_ENCRYPTION_KEY' in env and 'PGHOST' in env
        assert 'DOCS_WIKI_CHAT_MODEL' not in env and 'CODE_AGENT_MODEL' not in env
        assert 'DOCS_WIKI_LLM_API_KEY' not in env and 'CODE_AGENT_API_KEY' not in env


def test_gateways_accept_large_session_cookies():
    # Browsers send every localhost cookie to every port: the reader and admin
    # oauth2-proxy sessions (~3.5 KB each) plus Keycloak/Phoenix cookies exceed
    # nginx's default 8 KB header buffer and every page returns 400.
    for name in ("nginx.conf", "nginx-admin.conf"):
        conf = (DEPLOY / name).read_text(encoding="utf-8")
        assert "large_client_header_buffers 4 32k;" in conf, name


def test_host_folders_come_from_data_root_not_the_checkout():
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    assert ".local" not in text
    services = yaml.safe_load(text)["services"]
    for service in services.values():
        for volume in service.get("volumes", []):
            source = volume["source"] if isinstance(volume, dict) else volume.split(":/work/")[0]
            if source.startswith("${"):
                assert ROOT in source, source


def test_data_root_is_mounted_once_read_only_with_writable_folders_only_for_writers():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    for name, variables in WRITERS.items():
        assert data_mounts(services[name]) == [DATA, *(writable(v) for v in variables)], name
    # docs-wiki serves only the docs folder, so it sees nothing else in DATA_ROOT.
    assert data_mounts(services["docs-wiki"]) == [{"type": "bind", "source": ROOT + "/" + folder("DOCS_FOLDER"),
                                                    "target": "/work/data/" + folder("DOCS_FOLDER"), "read_only": True}]
    for name, service in services.items():
        if name not in WRITERS and name != "docs-wiki":
            assert not data_mounts(service), name
        for volume in service.get("volumes", []):
            target = volume["target"] if isinstance(volume, dict) else volume.split(":")[1]
            assert (not target.startswith("/work/") or target.startswith("/work/data")
                    or target == "/work/agent-workspaces"), (name, target)


def test_services_compute_their_roots_from_the_folder_variables():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    expected = {"DATA_ROOT_DIR": "/work/data", "DATA_ROOT": ROOT,
                "CODE_ROOT": "/work/data/" + folder("CODE_FOLDER"),
                "CODE_INDEX_ROOT": "/work/data/" + folder("CODE_INDEX_FOLDER"),
                "BOOTSTRAP_DOCS_ROOT": "/work/data/" + folder("DOCS_FOLDER"),
                "DOCUMENT_OUTPUT_ROOT": "/work/data/" + folder("GENERATED_DOCS_FOLDER"),
                "JIRA_EXPORT_ROOT": "/work/data/" + folder("JIRA_FOLDER")}
    removed = {"RAG_SOURCE_ROOT", "RG_DATA", "CODE_PATH", "CODE_INDEX_PATH", "GENERATED_DOCS_PATH", "DOCS_PATH"}
    for name in ("rag", "rag-worker", "admin-api", "repository-worker", "documentation-worker"):
        env = services[name]["environment"]
        assert {key: env[key] for key in expected} == expected, name
        assert not removed & set(env), name
    assert "environment" not in services["shell-worker"]  # it reads DATA_ROOT_DIR's default, /work/data


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker compose renders the commands")
def test_docs_commands_serve_the_docs_folder_inside_the_data_root(tmp_path):
    """The images' default /work/docs is not mounted; Compose points both servers at DOCS_FOLDER."""
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    required = sorted(set(re.findall(r"\$\{([A-Z0-9_]+):\?", text)) - {"DATA_ROOT"})
    env = tmp_path / ".env"
    env.write_text("".join(f"{key}=x\n" for key in required)
                   + f"DATA_ROOT={tmp_path / 'data'}\nDOCS_FOLDER=manuals/bootstrap\n", encoding="utf-8")
    result = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", str(DEPLOY / "compose.yaml"),
                             "config", "--format", "json"], capture_output=True, text=True, check=True)
    services = json.loads(result.stdout)["services"]
    assert services["docs-wiki"]["command"] == ["serve", "/work/data/manuals/bootstrap", "--host", "0.0.0.0"]
    assert services["admin-api"]["command"] == ["/work/data/manuals/bootstrap", "--host", "0.0.0.0"]
    for name in ("docs-wiki", "admin-api"):
        dockerfile = (DEPLOY / f"Dockerfile.{name}").read_text(encoding="utf-8")
        assert "/work/data/docs" in dockerfile and '"/work/docs"' not in dockerfile, name
