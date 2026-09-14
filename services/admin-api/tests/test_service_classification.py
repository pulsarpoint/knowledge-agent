"""Service origin/role validation, persistence, and migration against PostgreSQL."""

from pathlib import Path
from uuid import UUID

import psycopg
import pytest

from test_api import _client
from test_repositories import HEADERS

MIGRATIONS = Path(__file__).resolve().parents[3] / "db/migrations"


def test_classification_round_trip_preserves_omitted_fields_and_rejects_stale_updates(store, tmp_path):
    client = _client(tmp_path, repository_store=store)
    response = client.post("/api/services", headers=HEADERS, json={
        "name": "Nginx", "service_type": "builtin", "service_role": "proxy",
    })
    assert response.status_code == 201, response.text
    service_id = response.json()["id"]
    asset = client.get("/api/services", headers=HEADERS).json()["services"][0]
    assert asset["service_type"] == "builtin" and asset["service_role"] == "proxy"
    context = client.get(f"/api/services/{service_id}/context", headers=HEADERS).json()["service"]
    assert context == asset
    # Older clients editing another field must not reset a saved classification.
    response = client.put(f"/api/services/{service_id}", headers=HEADERS, json={"name": "Nginx", "owner": "Ops", "revision": 1})
    assert response.status_code == 200, response.text
    current = client.get("/api/services", headers=HEADERS).json()["services"][0]
    assert (current["service_type"], current["service_role"], current["owner"]) == ("builtin", "proxy", "Ops")
    value = {"name": "Nginx", "service_role": "web_server", "revision": 2}
    assert client.put(f"/api/services/{service_id}", headers=HEADERS, json=value).status_code == 200
    assert client.put(f"/api/services/{service_id}", headers=HEADERS, json=value).status_code == 409
    with store.connect() as conn:
        saved = conn.execute("SELECT service_type,service_role FROM ka.services WHERE id=%s", (UUID(service_id),)).fetchone()
        assert saved == {"service_type": "builtin", "service_role": "web_server"}
        events = conn.execute("SELECT details FROM ka.audit_events WHERE resource_id=%s ORDER BY created_at", (UUID(service_id),)).fetchall()
        assert events[-1]["details"]["service_role"] == "web_server"
        assert events[-1]["details"]["service_type"] == "builtin"


@pytest.mark.parametrize("field,value", [("service_type", "invalid"), ("service_role", "invalid"), ("service_type", None), ("service_role", None)])
def test_invalid_classifications_are_rejected_before_database(tmp_path, field, value):
    response = _client(tmp_path).post("/api/services", headers=HEADERS, json={"name": "Test", field: value})
    assert response.status_code == 400


def test_database_enums_reject_unknown_values(store):
    with store.connect() as conn:
        project = store.project(conn, "shop")
        with pytest.raises(psycopg.errors.InvalidTextRepresentation), conn.transaction():
            conn.execute("INSERT INTO ka.services(project_id,name,service_type) VALUES (%s,'Bad','invalid')", (project,))
        with pytest.raises(psycopg.errors.InvalidTextRepresentation), conn.transaction():
            conn.execute("INSERT INTO ka.services(project_id,name,service_role) VALUES (%s,'Bad','invalid')", (project,))
        assert conn.execute("SELECT count(*) AS n FROM ka.services").fetchone()["n"] == 0


def test_migration_preserves_inventory_and_refuses_to_discard_saved_classifications(store):
    up = (MIGRATIONS / "0044_service_classification.up.sql").read_text(encoding="utf-8")
    down = (MIGRATIONS / "0044_service_classification.down.sql").read_text(encoding="utf-8")
    with store.connect() as conn:
        conn.execute(down, prepare=False)
        project = store.project(conn, "shop")
        service_id = conn.execute("INSERT INTO ka.services(project_id,name,description) VALUES (%s,'Existing','Retained') RETURNING id", (project,)).fetchone()["id"]
        conn.execute(up, prepare=False)
        row = conn.execute("SELECT * FROM ka.service_assets WHERE id=%s", (service_id,)).fetchone()
        assert (row["name"], row["description"], row["revision"]) == ("Existing", "Retained", 1)
        assert (row["service_type"], row["service_role"]) == ("unclassified", "unclassified")
        conn.execute("UPDATE ka.services SET service_type='builtin',service_role='database' WHERE id=%s", (service_id,))
        conn.commit()
        with pytest.raises(psycopg.errors.RaiseException, match="Service classifications exist"):
            conn.execute(down, prepare=False)
        conn.rollback()
        assert conn.execute("SELECT service_role FROM ka.services WHERE id=%s", (service_id,)).fetchone()["service_role"] == "database"
        conn.execute("UPDATE ka.services SET service_type='unclassified',service_role='unclassified' WHERE id=%s", (service_id,))
        conn.execute(down, prepare=False)
        assert conn.execute("SELECT description FROM ka.services WHERE id=%s", (service_id,)).fetchone()["description"] == "Retained"
        conn.execute(up, prepare=False)
