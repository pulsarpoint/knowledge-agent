"""Repository integration tests always use an isolated, disposable PostgreSQL instance."""

import subprocess
import time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from knowledge_admin.repositories import RepositoryStore


@pytest.fixture(scope="session")
def postgres():
    name = f"ka-v3-repository-test-{uuid4().hex[:12]}"
    subprocess.run(["docker", "run", "-d", "--rm", "--name", name,
                    "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p", "127.0.0.1::5432",
                    "--tmpfs", "/var/lib/postgresql/data", "knowledge-agent-v3-postgres:17.10-vector0.8.6"],
                   check=True, capture_output=True)
    try:
        port = subprocess.run(["docker", "port", name, "5432"], check=True,
                              capture_output=True, text=True).stdout.strip().rsplit(":", 1)[1]
        conninfo = f"host=127.0.0.1 port={port} user=postgres dbname=postgres connect_timeout=2"
        for _ in range(40):
            try:
                with psycopg.connect(conninfo):
                    break
            except psycopg.OperationalError:
                time.sleep(0.25)
        else:
            pytest.fail("Disposable PostgreSQL did not become ready")
        yield conninfo
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)


@pytest.fixture
def store(postgres):
    database = f"test_{uuid4().hex}"
    with psycopg.connect(postgres, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    conninfo = f"{postgres} dbname={database}"
    try:
        with psycopg.connect(conninfo) as conn:
            for migration in sorted((Path(__file__).resolve().parents[3] / "db/migrations").glob("*.up.sql")):
                conn.execute(migration.read_text(), prepare=False)
        yield RepositoryStore(conninfo, b"k" * 32, "http://identity.test/realms/docs")
    finally:
        with psycopg.connect(postgres, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
