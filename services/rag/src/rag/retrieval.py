"""Hybrid retrieval: BM25 + vector, fused with reciprocal rank fusion."""
from __future__ import annotations

from rag.embed_client import EmbedError
from rag.store import Store


def rrf(ranked_lists: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda item: (-scores[item], item))


def retrieve(store: Store, client, question: str,
             top: int = 8) -> list[dict]:
    """Hybrid BM25+vector search fused with RRF. Returns rowid-keyed dicts
    with `path`, `heading_path`, `text` (the full chunk text, not an FTS
    snippet) and `title`, in fused rank order. Raises `EmbedError` (never
    a bare exception) when the embedding endpoint itself is unreachable
    or malformed, so callers can fall back to BM25-only search."""
    vectors = client.embed([question])
    # An HTTP-200 response is not a guarantee the payload is well-formed:
    # a misconfigured/misbehaving embedding endpoint can return an empty
    # `data` array, which EmbedClient.embed happily turns into `[]` rather
    # than raising. Without this check, `vectors[0]` below IndexErrors
    # straight out of the search route, which only catches EmbedError.
    if not vectors:
        raise EmbedError("embedding endpoint returned no data")
    embedding = vectors[0]
    vector_ids = store.vector_rowids(embedding, limit=20)
    bm25_ids = store.bm25_rowids(question, limit=20)
    fused = rrf([vector_ids, bm25_ids])[:top]
    return store.hydrate_chunk_rowids(fused)
