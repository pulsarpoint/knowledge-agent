from types import SimpleNamespace

from rag import config, embed_client, ingest
from rag.store import Store

DOC = """# Alpha

## Usage

Run the alpha refund pipeline.

## Setup

Install the alpha tool.
"""


def make_docs(tmp_path):
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "alpha.md").write_text(DOC, encoding="utf-8")
    return docs


def make_client(fake_embed):
    transport, state = fake_embed
    cfg = config.resolve_embed({"embed_base_url": "http://x/v1",
                                "embed_model": "fake-embed"}, env={})
    return embed_client.EmbedClient(cfg, transport=transport), state


def vector_count(docs):
    with Store.open(docs) as store:
        return store.conn.execute(
            "SELECT count(*) FROM chunk_vectors").fetchone()[0]


def test_embed_input_prefixed_with_heading_path(tmp_path, fake_embed):
    # DOC's intro paragraph precedes any heading ("(intro)" section) and
    # its "## Usage"/"## Setup" bodies are real sections -- one doc
    # exercises both the prefixed and unprefixed embed-input rules in a
    # single batch.
    docs = tmp_path / "docs"
    (docs / "how-to").mkdir(parents=True)
    (docs / "how-to" / "alpha.md").write_text(
        "# Alpha\n\nIntro paragraph.\n\n## Usage\n\n"
        "Run the alpha refund pipeline.\n\n## Setup\n\n"
        "Install the alpha tool.\n", encoding="utf-8")
    client, state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    sent = set(state["embed_batches"][0])
    assert "Intro paragraph." in sent  # "(intro)" chunk: unprefixed
    assert "Usage\n\nRun the alpha refund pipeline." in sent
    assert "Setup\n\nInstall the alpha tool." in sent
    # Never the bare (unprefixed) text for a real, non-"(intro)" section.
    assert "Run the alpha refund pipeline." not in sent
    assert "Install the alpha tool." not in sent


def test_initial_embed_and_skip(tmp_path, fake_embed):
    docs = make_docs(tmp_path)
    client, state = make_client(fake_embed)
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2 and counts["pending"] == 0
    assert vector_count(docs) == 2
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 0 and counts["unchanged"] == 1


def test_backfill_after_enabling_llm(tmp_path, fake_embed):
    docs = make_docs(tmp_path)
    counts = ingest.index_collection(docs)          # Phase 1 style, no embed
    assert counts["embedded"] == 0 and counts["pending"] == 0
    client, _ = make_client(fake_embed)
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2            # unchanged docs, missing vectors


def test_model_change_invalidates(tmp_path, fake_embed):
    docs = make_docs(tmp_path)
    client, state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    client.cfg.embed_model = "different-model"
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2            # full re-embed
    with Store.open(docs) as store:
        assert store.get_meta("embed_model") == "different-model"


def test_endpoint_down_leaves_pending(tmp_path, fake_embed):
    docs = make_docs(tmp_path)
    client, state = make_client(fake_embed)
    state["fail_embed"] = True
    counts = ingest.index_collection(docs, embed=client)
    assert counts["pending"] == 2 and counts["embedded"] == 0
    state["fail_embed"] = False
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2 and counts["pending"] == 0


def test_batching_boundary(tmp_path, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    for i in range(70):
        (docs / f"d{i:02}.md").write_text(f"# D{i}\n\nBody {i}.\n",
                                          encoding="utf-8")
    client, state = make_client(fake_embed)
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 70
    assert [len(b) for b in state["embed_batches"]] == [64, 6]


def test_single_doc_edit_reembeds(tmp_path, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "only.md").write_text("# Only\n\n## A\n\nOriginal text.\n", encoding="utf-8")
    client, state = make_client(fake_embed)
    ingest.index_collection(docs, embed=client)
    (docs / "only.md").write_text("# Only\n\n## A\n\nCompletely new text.\n", encoding="utf-8")
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] >= 1
    from conftest import fake_embedding
    with Store.open(docs) as store:
        row = store.conn.execute(
            "SELECT c.rowid, c.heading_path, c.text FROM chunks c").fetchone()
        stored = store.conn.execute(
            "SELECT embedding FROM chunk_vectors WHERE rowid = ?",
            (row["rowid"],)).fetchone()
        import sqlite_vec
        # Embed INPUT is heading-path-prefixed for a non-"(intro)" chunk
        # (this doc's sole chunk is under "## A") -- the stored `text`
        # column itself stays bare; see test_embed_input_prefixed_with_
        # heading_path for the dedicated coverage of that rule.
        expected_input = (row["text"] if row["heading_path"] == "(intro)"
                          else f"{row['heading_path']}\n\n{row['text']}")
        assert stored[0] == sqlite_vec.serialize_float32(
            fake_embedding(expected_input))


def test_dimension_drift_invalidates(tmp_path, fake_embed):
    # Unlike test_model_change_invalidates, the configured model *name*
    # never changes here -- only the vector shape the endpoint returns
    # does (e.g. a backend swap behind the same alias). sync_vectors can
    # only discover that once it actually embeds something, so a new doc
    # is what triggers the batch that surfaces the drift.
    docs = make_docs(tmp_path)
    client, state = make_client(fake_embed)
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2 and counts["pending"] == 0
    assert vector_count(docs) == 2

    (docs / "how-to" / "beta.md").write_text(
        "# Beta\n\n## Body\n\nBeta content.\n", encoding="utf-8")
    state["embed_dim"] = 4
    counts = ingest.index_collection(docs, embed=client)
    # This pass only has the new chunk missing; embedding it exposes the
    # dimension mismatch against the stored (8-dim) table, which gets
    # dropped and recreated at 4 dims, with this pass's one chunk inserted
    # into the fresh table. Counts are honest about *this* pass's work --
    # the two now-vector-less old chunks aren't "pending" here because
    # sync_vectors doesn't know about them yet.
    assert counts["embedded"] == 1 and counts["pending"] == 0
    assert vector_count(docs) == 1

    with Store.open(docs) as store:
        assert store.get_meta("embed_dim") == "4"

    # Follow-up pass: the two old chunks are now missing from the
    # (4-dim) chunk_vectors table and get re-embedded, at the now-current
    # dimension, with no drift left to detect. All three chunks end up
    # embedded across the two passes combined, with no permanent pending.
    counts = ingest.index_collection(docs, embed=client)
    assert counts["embedded"] == 2 and counts["pending"] == 0
    assert vector_count(docs) == 3


def test_malformed_embed_response_is_pending(tmp_path, fake_embed):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
    client, state = make_client(fake_embed)
    state["truncate_embeddings"] = True
    counts = ingest.index_collection(docs, embed=client)
    assert counts["pending"] >= 1
    assert counts["embedded"] == 0


class _MixedDimLLM:
    """Count-correct batch (so `sync_vectors`'s malformed-response check
    never trips) where exactly one vector's dimension disagrees with the
    rest -- an HTTP-200-but-malformed embed payload, `sync_vectors`'s own
    documented threat model (see its dimension-drift comment). `dim` is
    what `vectors[0]` (and therefore the batch) is embedded at; the chunk
    at `bad_index` gets `bad_dim` instead, so the `chunk_vectors` INSERT
    for that one row raises partway through the batch."""

    def __init__(self, model="fake-embed", dim=8, bad_index=1, bad_dim=3):
        self.cfg = SimpleNamespace(embed_model=model)
        self.dim = dim
        self.bad_index = bad_index
        self.bad_dim = bad_dim

    def embed(self, texts):
        from conftest import fake_embedding
        return [fake_embedding(t, self.bad_dim if i == self.bad_index
                               else self.dim)
                for i, t in enumerate(texts)]


def test_mixed_dimension_batch_keeps_drift_guard_armed(tmp_path):
    """Regression (rag-store Task 1 fix wave): a mid-batch dimension-
    mismatched vector must not leave embed_dim/embed_model meta unset
    after the resulting partial-INSERT failure. sync_vectors's meta
    writes must land BEFORE its chunk_vectors insert loop, not after --
    otherwise a raise partway through that loop (this test's scenario)
    skips the meta writes entirely, while the CREATE TABLE (and any
    inserts before the bad row) already happened and gets committed by
    index_collection regardless. That leaves a real chunk_vectors table
    at this dimension but embed_dim permanently unset, so the dimension-
    drift auto-recovery guard (gated on `stored_dim is not None`) could
    never fire again for this collection."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(
        "# A\n\n## One\n\nBody one.\n\n## Two\n\nBody two.\n",
        encoding="utf-8")
    llm = _MixedDimLLM(dim=8, bad_index=1, bad_dim=3)

    counts = ingest.index_collection(docs, embed=llm)
    assert counts["embedded"] == 0
    assert counts["pending"] == 2

    # Reopen fresh -- a separate connection, like any later `rag index`
    # run or served request would use -- and confirm the metas committed
    # despite the partial failure.
    with Store.open(docs) as store:
        assert store.get_meta("embed_dim") == "8"
        assert store.get_meta("embed_model") == "fake-embed"
