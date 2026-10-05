"""Deployment contracts for the independently runnable v3 bundle."""
import json
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
ROOT = "${DATA_ROOT:?Run make setup DATA_ROOT=/path}"


def host(variable: str, folder: str) -> str:
    """A host folder: its own override, else a subfolder of the required DATA_ROOT."""
    return "${" + variable + ":-" + ROOT + "/" + folder + "}"


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


def test_repository_worker_has_exclusive_write_mount_and_credentials():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    worker = services["repository-worker"]
    assert worker["volumes"] == [host("CODE_PATH", "code") + ":/work/code", host("CODE_INDEX_PATH", "code-index") + ":/work/code-index"]
    assert worker["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for name in ("admin-api", "repository-worker"):
        assert "CREDENTIAL_ENCRYPTION_KEY" in services[name]["environment"]
    for name in ("admin-web", "web"):
        assert "CREDENTIAL_ENCRYPTION_KEY" not in services[name].get("environment", {})


def test_agent_config_paths_match_code_and_document_mounts():
    services = yaml.safe_load((DEPLOY / 'compose.yaml').read_text())['services']
    for name in ('admin-api',):
        service = services[name]
        code_root = service['environment']['CODE_ROOT']
        rag_root = service['environment']['RAG_SOURCE_ROOT']
        assert code_root == services['repository-worker']['environment']['CODE_ROOT']
        assert rag_root == services['rag']['environment']['RAG_SOURCE_ROOT']
        assert host('CODE_PATH', 'code') + ':' + code_root + ':ro' in service['volumes']
        assert {'type': 'bind', 'source': host('RG_DATA', 'rag-data'),
                'target': rag_root, 'read_only': True} in service['volumes']


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


def test_documentation_worker_can_publish_to_rag():
    compose = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))
    env = compose["services"]["documentation-worker"]["environment"]
    assert env["DOCS_WIKI_RAG_URL"] == "http://rag:8790"
    assert env["RAG_BACKOFFICE_KEY"] == "${RAG_BACKOFFICE_KEY:?Run make setup}"


def test_host_folders_come_from_data_root_not_the_checkout():
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    assert ".local" not in text
    services = yaml.safe_load(text)["services"]
    for service in services.values():
        for volume in service.get("volumes", []):
            source = volume["source"] if isinstance(volume, dict) else volume.split(":/work/")[0]
            if source.startswith("${"):
                assert ROOT in source, source


def test_rag_reads_every_data_area_and_writes_only_rag_data():
    services = yaml.safe_load((DEPLOY / "compose.yaml").read_text())["services"]
    expected = {
        "/work/code": host("CODE_PATH", "code"), "/work/code-index": host("CODE_INDEX_PATH", "code-index"),
        "/work/generated-documents": host("GENERATED_DOCS_PATH", "generated-documents"), "/work/docs": host("DOCS_PATH", "docs"),
    }
    for name in ("rag", "rag-worker"):
        service = services[name]
        mounts = {(v["target"] if isinstance(v, dict) else v.split(":")[-2] if v.endswith(":ro") else v.split(":")[-1]): v for v in service["volumes"]}
        for target, source in expected.items():
            assert mounts[target] == f"{source}:{target}:ro", (name, target)
        rag = mounts["/work/rag-data"]
        assert isinstance(rag, dict) and not rag.get("read_only") and rag["source"] == host("RG_DATA", "rag-data")
        env = service["environment"]
        assert env["CODE_ROOT"] == "/work/code" and env["RAG_SOURCE_ROOT"] == "/work/rag-data"
        assert env["DOCUMENT_OUTPUT_ROOT"] == "/work/generated-documents" and env["BOOTSTRAP_DOCS_ROOT"] == "/work/docs"
        assert env["CODE_INDEX_ROOT"] == "/work/code-index"
        for key, folder in (("CODE_PATH", "code"), ("CODE_INDEX_PATH", "code-index"), ("RG_DATA", "rag-data"), ("GENERATED_DOCS_PATH", "generated-documents"), ("DOCS_PATH", "docs")):
            assert env[key] == host(key, folder)


def test_shell_worker_reads_every_collection_area_read_only():
    """Shell agents may be granted a collection in any area; each one must be mounted read-only."""
    volumes = yaml.safe_load((DEPLOY / "compose.yaml").read_text(encoding="utf-8"))["services"]["shell-worker"]["volumes"]
    for variable, folder in (("CODE_PATH", "code"), ("CODE_INDEX_PATH", "code-index"), ("RG_DATA", "rag-data"),
                             ("GENERATED_DOCS_PATH", "generated-documents"), ("DOCS_PATH", "docs")):
        assert host(variable, folder) + ":/work/" + folder + ":ro" in volumes
    assert "agent-workspaces:/work/agent-workspaces" in volumes
