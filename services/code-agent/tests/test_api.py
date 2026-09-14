import shutil

import pytest
from fastapi.testclient import TestClient

from code_agent import api, config


@pytest.fixture()
def client_and_repo(repo):
    cfg = config.Config(
        repos=[config.Repo(name="my-app", path=repo)])
    return TestClient(api.create_app(cfg)), repo


def test_health(client_and_repo):
    client, _repo = client_and_repo
    assert client.get("/health").json() == {"status": "ok"}


def test_repos_exact_keys(client_and_repo):
    client, _repo = client_and_repo
    body = client.get("/repos").json()
    assert body == [{"name": "my-app", "label": "my-app", "path_exists": True,
                     "backend": None}]
    assert set(body[0]) == {"name", "label", "path_exists", "backend"}


def test_repos_path_exists_flips_when_dir_removed(client_and_repo):
    client, repo = client_and_repo
    shutil.rmtree(repo)
    body = client.get("/repos").json()
    assert body == [{"name": "my-app", "label": "my-app", "path_exists": False,
                     "backend": None}]


def test_unknown_route_404_error_shape(client_and_repo):
    client, _repo = client_and_repo
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"message": "Not Found"}}


def test_wrong_method_405_error_shape_keeps_allow(client_and_repo):
    client, _repo = client_and_repo
    response = client.post("/repos")
    assert response.status_code == 405
    assert response.json()["error"]["message"]
    assert "GET" in response.headers.get("allow", "")


def test_code_question_is_todo_and_validated(client_and_repo):
    client, _ = client_and_repo
    response = client.post('/repos/my-app/ask', json={'question':'Inspect'})
    assert response.status_code == 200 and response.text == 'TODO'
    assert response.headers['content-type'].startswith('text/plain')
    assert client.post('/repos/missing/ask', json={'question':'Inspect'}).status_code == 404
    for question in ['', ' ', 'x' * 10001]:
        assert client.post('/repos/my-app/ask', json={'question':question}).status_code == 400
