from test_workspace import collection, configure, search, sync, write


def two_collections(env):
    roots = env[6]
    for name, text in (("docs", "# Payments\n\nRetries use exponential backoff."), ("jira", "# DEMO-1\n\nRetries fail on timeout.")):
        (roots["rag-data"] / name).mkdir()
        created = collection(env, name, f"rag-data/{name}")
        write(env, "a.md", text, name=name)
        sync(env, created)


def test_search_one_collection_or_all(env):
    two_collections(env)
    only = search(env, "retries", ["docs"]).json()
    assert {hit["collection"] for hit in only["results"]} == {"docs"} and only["collections"] == ["docs"]
    both = search(env, "retries").json()
    assert {hit["collection"] for hit in both["results"]} == {"docs", "jira"}
    assert search(env, "retries", []).json()["results"] == []
    missing = search(env, "retries", ["nope"])
    assert missing.status_code == 404 and "nope" in missing.json()["error"]["message"]


def test_semantic_collection_without_model_falls_back(env):
    client, prefix, roots = env[1], env[3], env[6]
    (roots["rag-data"] / "docs").mkdir()
    created = collection(env, search_mode="hybrid")
    write(env)
    sync(env, created)
    result = search(env, "retries", ["docs"]).json()
    assert result["fallback"] == ["docs"] and result["results"]
    forced = search(env, "retries", ["docs"], mode="embedding").json()
    assert forced["fallback"] == ["docs"] and forced["results"]


def test_hybrid_uses_vectors_when_embedded(env):
    roots, state = env[6], env[4]
    (roots["rag-data"] / "docs").mkdir()
    configure(env)
    created = collection(env, embeddings_enabled=True, search_mode="hybrid")
    write(env)
    sync(env, created)
    calls = len(state["calls"])
    result = search(env, "backoff", ["docs"]).json()
    assert result["fallback"] == [] and result["results"] and len(state["calls"]) == calls + 1
