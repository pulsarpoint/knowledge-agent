"""Environment CRUD, membership isolation and migration safety against PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS


@pytest.fixture
def inventory(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    host = client.post("/api/servers", headers=HEADERS, json={"name": "App host", "hostname": "app.internal"}).json()
    service = client.post("/api/services", headers=HEADERS, json={"name": "API"}).json()
    return client, service, host


def test_round_trip_shared_membership_edit_and_audit(store, inventory):
    client, service, host = inventory
    value = {"name": " Development ", "description": "For daily testing", "service_ids": [service["id"]], "server_ids": [host["id"]]}
    response = client.post("/api/environments", headers=HEADERS, json=value)
    assert response.status_code == 201, response.text
    environment = response.json()
    assert environment["name"] == "Development" and environment["revision"] == 1
    assert environment["services"] == [{"id": service["id"], "name": "API"}]
    assert environment["servers"] == [{"id": host["id"], "name": "App host", "hostname": "app.internal"}]
    assert "ssh" not in response.text and "credential" not in response.text
    second = client.post("/api/environments", headers=HEADERS, json={**value, "name": "Staging"}).json()
    assert second["services"] == environment["services"] and second["servers"] == environment["servers"]
    listing = client.get("/api/environments", headers=HEADERS).json()["environments"]
    assert [row["name"] for row in listing] == ["Development", "Staging"]
    url = f'/api/environments/{environment["id"]}'
    assert client.get(url, headers=HEADERS).json() == environment
    edited = client.put(url, headers=HEADERS, json={"name": "Dev", "description": "Updated", "revision": 1, "service_ids": [], "server_ids": [host["id"]]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["revision"] == 2 and edited.json()["services"] == []
    assert client.get(f'/api/environments/{second["id"]}', headers=HEADERS).json() == second
    assert client.put(url, headers=HEADERS, json={**value, "revision": 1}).status_code == 409
    assert client.get(url, headers=HEADERS).json() == edited.json()
    with store.connect() as conn:
        events = conn.execute("SELECT action,details FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at", (UUID(environment["id"]),)).fetchall()
        assert [row["action"] for row in events] == ["environment.created", "environment.updated"]
        assert events[-1]["details"]["server_ids"] == [host["id"]]
        assert events[-1]["details"]["service_ids"] == []


def test_delete_requires_confirmation_revision_and_preserves_inventory(store, inventory):
    client, service, host = inventory
    # The existing deployment label is not an environment membership inference.
    assert client.put(f'/api/services/{service["id"]}', headers=HEADERS, json={"name": "API", "revision": 1,
        "deployments": [{"server_id": host["id"], "environment": "dev"}]}).status_code == 200
    assert client.get("/api/environments", headers=HEADERS).json() == {"environments": []}
    environment = client.post("/api/environments", headers=HEADERS, json={"name": "Dev", "service_ids": [service["id"]], "server_ids": [host["id"]]}).json()
    url = f'/api/environments/{environment["id"]}'
    assert client.delete(url + "?revision=1", headers=HEADERS).status_code == 400
    assert client.delete(url + "?revision=2&confirmed=true", headers=HEADERS).status_code == 409
    assert client.get(url, headers=HEADERS).status_code == 200
    assert client.delete(url + "?revision=1&confirmed=true", headers=HEADERS).status_code == 200
    assert client.get(url, headers=HEADERS).status_code == 404
    assert client.get("/api/environments", headers=HEADERS).json() == {"environments": []}
    with store.connect() as conn:
        for table in ("environment_services", "environment_servers"):
            assert conn.execute(f"SELECT count(*) AS n FROM ka.{table}").fetchone()["n"] == 0
        for table in ("services", "servers", "service_deployments"):
            assert conn.execute(f"SELECT count(*) AS n FROM ka.{table}").fetchone()["n"] == 1
        assert conn.execute("SELECT action FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at DESC LIMIT 1", (UUID(environment["id"]),)).fetchone()["action"] == "environment.deleted"


def test_member_deletion_removes_only_memberships(inventory):
    client, service, host = inventory
    environment = client.post("/api/environments", headers=HEADERS, json={"name": "Dev", "service_ids": [service["id"]], "server_ids": [host["id"]]}).json()
    current_service = client.get(f'/api/services/{service["id"]}/context', headers=HEADERS).json()['service']
    assert client.delete(f'/api/services/{service["id"]}?revision={current_service["revision"]}&confirmed=true', headers=HEADERS).status_code == 200
    assert client.delete(f'/api/servers/{host["id"]}?revision=1&confirmed=true', headers=HEADERS).status_code == 200
    current = client.get(f'/api/environments/{environment["id"]}', headers=HEADERS).json()
    assert current["name"] == "Dev" and current["services"] == [] and current["servers"] == []


def test_foreign_members_and_environment_are_rejected_by_api_and_foreign_keys(store, inventory):
    client, service, host = inventory
    with store.connect() as conn:
        foreign = store.project(conn, "foreign")
        foreign_service = conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Other') RETURNING id", (foreign,)).fetchone()["id"]
        foreign_server = conn.execute("INSERT INTO ka.servers(project_id,name,hostname,enabled) VALUES (%s,'Other','other.internal',false) RETURNING id", (foreign,)).fetchone()["id"]
        foreign_environment = conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'Secret') RETURNING id", (foreign,)).fetchone()["id"]
    for field, member in (("service_ids", foreign_service), ("server_ids", foreign_server), ("service_ids", uuid4()), ("server_ids", uuid4())):
        response = client.post("/api/environments", headers=HEADERS, json={"name": "Invalid", field: [str(member)]})
        assert response.status_code == 400, response.text
    url = f"/api/environments/{foreign_environment}"
    assert client.get(url, headers=HEADERS).status_code == 404
    assert client.put(url, headers=HEADERS, json={"name": "Other", "revision": 1}).status_code == 404
    assert client.delete(url + "?revision=1&confirmed=true", headers=HEADERS).status_code == 404
    assert client.get("/api/environments", headers=HEADERS).json()["environments"] == []
    own = client.post("/api/environments", headers=HEADERS, json={"name": "Own"}).json()
    with store.connect() as conn:
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute("INSERT INTO ka.environment_services(project_id,environment_id,service_id) VALUES (%s,%s,%s)", (UUID(own["project_id"]), UUID(own["id"]), foreign_service))
        with pytest.raises(psycopg.errors.ForeignKeyViolation), conn.transaction():
            conn.execute("INSERT INTO ka.environment_servers(project_id,environment_id,server_id) VALUES (%s,%s,%s)", (UUID(own["project_id"]), UUID(own["id"]), foreign_server))


def test_duplicate_names_and_concurrent_updates_cannot_overwrite_each_other(inventory):
    client, service, host = inventory
    environment = client.post("/api/environments", headers=HEADERS, json={"name": "Dev"}).json()
    assert client.post("/api/environments", headers=HEADERS, json={"name": "dev"}).status_code == 409
    def save(description):
        return client.put(f'/api/environments/{environment["id"]}', headers=HEADERS,
                          json={"name": "Dev", "revision": 1, "description": description}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, ["First", "Second"])) == [200, 409]


@pytest.mark.parametrize("value", [{"name": " "}, {"name": "x" * 101}, {"name": "Bad\x00"}, {"name": "Dev", "description": "x" * 4001},
    {"name": "Dev", "service_ids": [str(UUID(int=1))] * 2}, {"name": "Dev", "server_ids": [str(UUID(int=1))] * 2}])
def test_invalid_inputs_are_rejected_before_storage(tmp_path, value):
    assert _client(tmp_path).post("/api/environments", headers=HEADERS, json=value).status_code == 400


@pytest.mark.parametrize("method,url", [("GET", "/api/environments"), ("POST", "/api/environments"),
    ("GET", f"/api/environments/{UUID(int=1)}"), ("PUT", f"/api/environments/{UUID(int=1)}"),
    ("DELETE", f"/api/environments/{UUID(int=1)}?revision=1&confirmed=true")])
def test_all_routes_require_admin_before_data_access(tmp_path, method, url):
    assert _client(tmp_path).request(method, url, json={"name": "Dev"}).status_code == 403


def test_migration_reapply_and_populated_rollback_protection(store):
    migrations = Path(__file__).resolve().parents[3] / "db/migrations"
    down = (migrations / "0045_environments.down.sql").read_text(encoding="utf-8")
    up = (migrations / "0045_environments.up.sql").read_text(encoding="utf-8")
    with store.connect() as conn:
        project = store.project(conn, "shop")
        conn.execute("INSERT INTO ka.services(project_id,name) VALUES (%s,'Retained')", (project,))
        conn.execute((migrations / "0050_service_environments.down.sql").read_text(), prepare=False)
        conn.execute((migrations / "0049_layered_environment_architecture.down.sql").read_text(), prepare=False)
        conn.execute((migrations / "0048_server_infrastructure_documentation.down.sql").read_text(), prepare=False)
        conn.execute((migrations / "0047_documentation_workflows.down.sql").read_text(), prepare=False)
        conn.execute((migrations / "0046_documentation_generation.down.sql").read_text(), prepare=False)
        conn.execute(down, prepare=False)
        conn.execute(up, prepare=False)
        assert conn.execute("SELECT name FROM ka.services").fetchone()["name"] == "Retained"
        assert conn.execute("SELECT count(*) AS n FROM ka.environments").fetchone()["n"] == 0
        conn.execute("INSERT INTO ka.environments(project_id,name) VALUES (%s,'Dev')", (project,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="Environments exist"):
            conn.execute(down, prepare=False)
        conn.rollback()
        assert conn.execute("SELECT name FROM ka.environments").fetchone()["name"] == "Dev"
