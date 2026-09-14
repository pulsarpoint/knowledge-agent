from contextlib import contextmanager
from uuid import uuid4

from code_agent import inventory
from code_agent.api import create_app
from code_agent.config import Config
from fastapi.testclient import TestClient


def test_inventory_updates_between_requests_and_rejects_paths_outside_mount(tmp_path, monkeypatch):
    root = tmp_path / 'code'; root.mkdir()
    checkout = root / 'repository' / 'checkouts' / 'one'; checkout.mkdir(parents=True)
    outside = tmp_path / 'outside'; outside.mkdir()
    (root / 'escape').symlink_to(outside, target_is_directory=True)
    repo_id = uuid4()
    rows = [{'id': repo_id, 'name': 'Payments', 'folder_paths': ['repository/checkouts/one']},
            {'id': uuid4(), 'name': 'Escape', 'folder_paths': ['escape']},
            {'id': uuid4(), 'name': 'Parent', 'folder_paths': ['../outside']}]
    class Connection:
        def execute(self, query):
            assert query == 'SET TRANSACTION READ ONLY' or 'archived_at IS NULL' in query
            return self
        def fetchall(self): return rows
    @contextmanager
    def connect(*args, **kwargs): yield Connection()
    monkeypatch.setattr(inventory.psycopg, 'connect', connect)
    monkeypatch.setenv('CODE_ROOT', str(root))
    client = TestClient(create_app(Config(repos=[])))
    assert client.get('/repos').json() == [{'name': str(repo_id), 'label': 'Payments', 'path_exists': True, 'backend': None}]
    rows.clear()
    assert client.get('/repos').json() == []
    assert client.post(f'/repos/{repo_id}/ask', json={'question': 'read'}).status_code == 404
