import pytest

from rag import config, embed_client, ingest, retrieval
from rag.store import Store


def test_rrf_deterministic():
    fused = retrieval.rrf([[1, 2, 3], [3, 1, 4]], k=60)
    assert fused[0] == 1 and 3 in fused[:2] and fused[-1] in (2, 4)


def make_client(fake_embed):
    transport, state = fake_embed
    cfg = config.EmbedConfig(base_url="http://x/v1", api_key=None,
                             embed_model="fake-embed")
    return embed_client.EmbedClient(cfg, transport=transport), state


def test_retrieve_returns_hybrid_hits_in_fused_rank_order(tmp_path, fake_embed):
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "refunds.md").write_text(
        "# Refunds\n\n## Requesting\n"
        "Refunds are asynchronous via the refund-events topic.\n",
        encoding="utf-8")
    (docs / "index.md").write_text("# Overview\n\nThe map.\n", encoding="utf-8")
    client, _state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    with Store.open(docs) as store:
        hits = retrieval.retrieve(store, client, "refund topic", top=8)
    assert hits
    assert hits[0]["path"] == "how-to/refunds.md"
    assert hits[0]["heading_path"] == "Requesting"
    # Full chunk text, not an FTS snippet -- no <mark> highlighting.
    assert hits[0]["text"] == (
        "Refunds are asynchronous via the refund-events topic.")
    assert hits[0]["title"] == "Refunds"
    assert set(hits[0]) == {"rowid", "path", "heading_path", "text", "title"}


def test_retrieve_raises_embed_error_on_empty_embedding_response(
        tmp_path, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody text.\n", encoding="utf-8")
    client, state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    state["truncate_embeddings"] = True
    with Store.open(docs) as store:
        with pytest.raises(embed_client.EmbedError):
            retrieval.retrieve(store, client, "body", top=8)


def test_retrieve_empty_index_returns_no_hits(tmp_path, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    client, _state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    with Store.open(docs) as store:
        assert retrieval.retrieve(store, client, "anything", top=8) == []
