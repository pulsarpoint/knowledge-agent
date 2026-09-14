"""The indexing pipeline: walk a collection's markdown files, parse front
matter and structure, chunk, render, write through `Store`, and (when an
embed client is configured) sync vectors.

Moved verbatim off the old single-module indexing pipeline (store-package
refactor, see `docs/superpowers/plans/2026-08-04-rag-store-package.md`) --
the last piece of that module, now that its content helpers live in
`rag.content` and its SQL lives in `rag.store`. `split_sections`/
`chunk_sections` stay here rather than moving to `rag.content`: they are
the "structure -> chunk" step of THIS pipeline specifically (only ever
called from `index_collection`, one document at a time), not
general-purpose text helpers reused elsewhere the way
`render_html`/`parse_front_matter` are -- a disclosed judgment call, see
task-2-report.md.

**Chunk-contract warnings (2026-08, see
`docs/superpowers/plans/2026-08-04-chunk-contract.md`).** `index_collection`
now emits three deterministic warning classes straight to stderr while it
walks each (re)indexed doc -- section-split, heading-less-doc, duplicate-
heading-text -- every fact for which the pipeline already computes as a
side effect of parsing/chunking (`structure.parse_structure`'s `headings`/
`sections` and `_chunk_sections_with_facts`'s per-section split record).
No second parse, no heuristics. `index_collection` therefore calls
`parse_structure` directly (instead of routing through `split_sections`,
which only exposes `(title, sections)`) so it also has `doc.headings` on
hand for the duplicate-heading check -- one parse, same as before;
`split_sections` itself is unchanged and kept as a thin, independently
tested wrapper (nothing outside `ingest.py`/tests calls it, but it's a
reasonable public seam and existing tests pin its 2-tuple return).
Warn-NEVER-fail: a violating doc indexes exactly as it would otherwise;
warnings are purely observational (see `_emit_doc_warnings`).
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
from collections import Counter
from pathlib import Path

from rag.content import parse_front_matter, quadrant_for, render_html
from rag.store import Store
from rag.store.store import load_vec
from rag.structure import DocStructure, Section, parse_structure

logger = logging.getLogger("rag.ingest")

# The chunker's cap/overlap: a section longer than CAP gets split;
# OVERLAP is the character budget the old hard-slice fallback slides
# back by between consecutive slices (see `_hard_slice`). Named so
# `index_collection` and `chunk_sections`'s own default parameters can't
# drift apart -- both read from here.
CAP = 2000
OVERLAP = 200

# A "paragraph" for splitting purposes is text between blank-line
# boundaries -- one or more blank-ish lines (allows trailing whitespace
# on an otherwise-blank line) -- computed line-by-line by
# `_fence_aware_paragraphs` below rather than a single whole-text regex
# split, so a blank line inside an open fence can be recognized and
# excluded as a candidate boundary (see that function's docstring).

# Fence-delimiter line matcher: the FULL run of backticks or tildes at
# line start (three or more), not just a fixed 3-character prefix. This
# follows CommonMark: a fence only closes on a line whose delimiter run
# uses the same character and is at least as long as the opening run.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def split_sections(body: str) -> tuple[str, list[Section]]:
    """Title + chunk-ready sections, derived from the single shared parse
    in `structure.parse_structure` (see that module's docstring for the
    exact section-splitting semantics ported from the old line scanner).
    Kept as a convenience wrapper (see module docstring) -- `index_collection`
    calls `parse_structure` directly so it can also reach `doc.headings`.
    """
    doc = parse_structure(body)
    return doc.title, doc.sections


def _hard_slice(text: str, cap: int, overlap: int) -> list[str]:
    """The ORIGINAL (pre-chunk-contract) chunking algorithm verbatim: a
    contiguous sliding window, each slice <= cap, each slice after the
    first starting `overlap` chars back into the previous one. Still the
    exact algorithm applied to (a) a section with no usable blank-line
    paragraph boundary at all, and (b) any single paragraph that alone
    exceeds cap even after paragraph-boundary splitting -- see
    `_split_oversized`."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + cap, len(text))
        pieces.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return pieces


def _fence_aware_paragraphs(text: str) -> list[str]:
    r"""Split `text` into paragraphs at blank-line boundaries -- one or
    more consecutive blank-ish lines -- EXCEPT a blank-line run that
    falls inside an open fence is not treated as a boundary at all: the
    surrounding content (including the fence's own open/close delimiter
    lines) stays one paragraph, so `_split_oversized`'s packer can never
    choose a chunk boundary that lands between an opening and closing
    fence delimiter (review fix-wave, Important 2: a mid-fence split
    degrades both the embedded text and any LLM tool-context reading a
    chunk back out).

    Fence state: real CommonMark fence-close matching (character AND
    run-length, `_FENCE_RE`) -- NOT a same-prefix parity
    count. A first-cut version of this function DID use a naive parity
    count (any line starting with ``` or ~~~ toggles "inside a fence"),
    which breaks on a WIDENED fence: a CommonMark-valid
    idiom where the opener is deliberately
    made longer (e.g. ```` -- four backticks) specifically so a literal
    ``` run inside the fenced content (an embedded fence example, a doc
    comment quoting one, etc.) can never prematurely close it. Under
    parity-counting, that inner ``` line was WRONGLY counted as a second
    delimiter, flipping "inside fence" state early -- and because state
    is a simple parity flag, that desync CASCADES silently past the
    fence's real close for the rest of `text`, suppressing every
    legitimate paragraph boundary after it (a document-wide corruption
    from one nested fence, strictly worse for that document than the
    pre-fence-fix behavior). The state machine here tracks the OPEN
    fence's exact character and run length and only closes on a line
    that matches both (same character, run length >= the opening run's)
    -- a shorter same-character run (``` inside a widened ```` fence) or
    a different-character run (~~~ inside a ``` fence) is correctly left
    as fence content, never touching state.

    This walks `text` exactly once, line by line, grouping consecutive
    non-boundary lines together and re-joining each group with `"\n"`.
    For text with no fence-delimiter LINE at all (the overwhelming common
    case for ordinary prose) fence state never changes and this
    reproduces a plain `r"\n\s*\n"` whole-text split's fragments exactly,
    byte for byte (deliberately NOT `.strip()`-ing each paragraph,
    matching that former approach) -- for text WITH fence-delimiter
    lines, correctness now depends on the real character+length matching
    above, not merely on their absence.

    If EVERY blank line in `text` turns out to be fence-internal (a
    section that's essentially one giant fenced block), this returns
    exactly one paragraph (the whole text, fence markers and all) --
    `_split_oversized` below then has no safe boundary to work with and
    falls through to the same hard-slice-with-overlap fallback it
    already uses for a single paragraph that alone exceeds cap (a
    mid-fence split THERE is accepted and disclosed: never fail is more
    important than never producing an unsafe boundary when no safe one
    exists)."""
    lines = text.split("\n")
    groups: list[list[str]] = [[]]
    in_fence = False
    fence_delim = ""
    for line in lines:
        inside_fence = in_fence
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            delim = fence_match.group(1)
            if not in_fence:
                in_fence = True
                fence_delim = delim
            elif delim[0] == fence_delim[0] and len(delim) >= len(fence_delim):
                in_fence = False
                fence_delim = ""
            # else: a differing delimiter character, or a same-character
            # run shorter than the one that opened the fence (e.g. a ```
            # line inside a widened ```` block), is just fence content,
            # not a close -- see `_FENCE_RE`'s comment.
        if line.strip() == "" and not inside_fence:
            groups.append([])
        else:
            groups[-1].append(line)
    return ["\n".join(group) for group in groups if group]


def _split_oversized(text: str, cap: int, overlap: int) -> list[str]:
    """Split `text` (already known to exceed `cap`) at blank-line
    paragraph boundaries (fence-aware, see `_fence_aware_paragraphs`),
    packing paragraphs greedily into each piece up to the boundary
    closest under `cap`, with the previous piece's LAST paragraph
    carried into the next piece as overlap (in the spirit of the old
    fixed `overlap`-char slide, at paragraph granularity instead of a
    fixed char count -- disclosed exact rule below). Falls back to the
    old hard-slice-with-overlap algorithm for text with no usable (safe)
    paragraph boundary at all, or for any single paragraph that alone
    exceeds `cap` (mid-stream: that one paragraph hard-slices on its own,
    accumulation resumes fresh with the paragraph after it -- no tail
    carried into or out of a hard-sliced run; two different overlap
    mechanisms, never blended).

    Exact tail-carry rule: closing a piece because the next paragraph
    would push it over `cap`, the just-closed piece's last paragraph is
    prepended to the next piece verbatim (not trimmed to `overlap` chars
    -- a paragraph is the atomic unit here) UNLESS that carried paragraph
    plus the next one would itself exceed `cap`, in which case the carry
    is dropped and the next piece starts fresh with just that paragraph
    (this is exactly what happens for two paragraphs that together
    already exceed `cap` on their own -- there is nothing under `cap` to
    carry without immediately reproducing the overflow that caused the
    split)."""
    paragraphs = _fence_aware_paragraphs(text)
    if not paragraphs:
        return _hard_slice(text, cap, overlap)

    pieces: list[str] = []
    current: list[str] = []
    for para in paragraphs:
        if len(para) > cap:
            if current:
                pieces.append("\n\n".join(current))
                current = []
            pieces.extend(_hard_slice(para, cap, overlap))
            continue
        if current and len("\n\n".join(current + [para])) > cap:
            pieces.append("\n\n".join(current))
            tail = current[-1]
            carried = f"{tail}\n\n{para}"
            current = [tail, para] if len(carried) <= cap else [para]
            continue
        current.append(para)
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def _chunk_sections_with_facts(
        sections: list[Section], cap: int = CAP, overlap: int = OVERLAP
) -> tuple[list[Section], list[tuple[str, int, int]]]:
    """`chunk_sections`'s real implementation, additionally returning the
    section-split warning facts `index_collection` needs: one
    `(heading_path, original_chars, resulting_chunk_count)` tuple per
    section that got split. Split out from the public `chunk_sections` so
    that function's return type (a bare `list[Section]`, pinned by
    existing tests/callers) never has to change to carry this data."""
    if overlap >= cap:
        raise ValueError("overlap must be smaller than cap")
    out: list[Section] = []
    facts: list[tuple[str, int, int]] = []
    for section in sections:
        if len(section.text) <= cap:
            out.append(section)
            continue
        pieces = _split_oversized(section.text, cap, overlap)
        out.extend(Section(heading_path=section.heading_path, text=piece)
                   for piece in pieces)
        facts.append((section.heading_path, len(section.text), len(pieces)))
    return out, facts


def chunk_sections(sections: list[Section], cap: int = CAP,
                   overlap: int = OVERLAP) -> list[Section]:
    chunks, _facts = _chunk_sections_with_facts(sections, cap, overlap)
    return chunks


def _emit_doc_warnings(collection: str, rel: str, doc: DocStructure,
                       split_facts: list[tuple[str, int, int]]) -> int:
    """Print the three chunk-contract warning classes for one (re)indexed
    doc straight to stderr in the brief's exact formats, and return how
    many lines were printed. Every fact here comes from `doc` (this doc's
    single `parse_structure` result) or `split_facts` (this doc's
    `_chunk_sections_with_facts` result) -- both already computed by the
    pipeline for chunking/rendering, never a second analysis pass.

    Heading-less doc: fires when `doc.headings` is EMPTY -- no heading of
    any kind, any level, root or not (not merely "no root H2/H3"). A doc
    with just an H1 title and no H2/H3 subsections still has ALL its body
    content fall into "(intro)" (root H1 never opens a section), but it
    is not what this warning means: it has a heading (the title), so it
    is not "heading-less" in the literal sense the class name promises.
    Only a doc with genuinely zero headings -- title included -- fires
    this one. (Guarded on non-empty `doc.sections` too: an empty doc, no
    body content at all, has nothing to warn is "not deep-linkable".)

    Duplicate heading text: fires per DISTINCT heading text that repeats,
    counted across `doc.headings` in full -- every level, root or nested
    inside a blockquote/list -- because that is exactly the population
    `render_html`'s slug-dedupe walk consumes (see `content.render_html`
    and `anchors._rendered_ids`'s docstrings): a repeat anywhere in that
    walk gets its anchor id suffixed, so the warning's "anchors suffixed"
    claim has to be checked against the same population. Matching is
    exact/case-sensitive text equality -- a deliberate, disclosed
    simplification (not slug equality, which would conflate e.g. "Setup"
    and "setup" under one count while displaying only one of their two
    distinct literal texts in the message).
    """
    count = 0
    if not doc.headings and doc.sections:
        print(f"rag index: warning: {collection}: {rel}: no headings -- "
              f"content not deep-linkable", file=sys.stderr)
        count += 1
    for text, n in Counter(h.text for h in doc.headings).items():
        if n > 1:
            print(f"rag index: warning: {collection}: {rel}: duplicate "
                  f"heading '{text}' (x{n}) -- anchors suffixed",
                  file=sys.stderr)
            count += 1
    for heading_path, chars, n_chunks in split_facts:
        print(f"rag index: warning: {collection}: {rel} > "
              f"'{heading_path}': {chars} chars, split into {n_chunks} "
              f"chunks", file=sys.stderr)
        count += 1
    return count


def _embed_input(row) -> str:
    """The text actually sent to the embed endpoint for one pending chunk
    row (`rowid`, `heading_path`, `text` -- see `Store.pending_chunks`).
    Heading-prefixed for any real section (`heading_path != "(intro)"`):
    the embedding model gets the same "what is this chunk about" context
    a reader gets from the page's own heading, which the bare chunk text
    alone doesn't carry. "(intro)" chunks are left unprefixed -- that
    label is a synthetic pre-first-heading placeholder, not real document
    content, and prefixing it would inject a token that means nothing to
    the model. This ONLY changes what gets embedded: the stored `chunks`
    row (`text` column, FTS-indexed) and everything else in the pipeline
    keep the bare chunk text untouched -- see module docstring."""
    heading_path = row["heading_path"]
    if heading_path == "(intro)":
        return row["text"]
    return f"{heading_path}\n\n{row['text']}"


def sync_vectors(store: Store, llm) -> tuple[int, int]:
    """Embed chunks whose vectors are missing; drop stale vectors.
    Returns (embedded, pending). Orchestration only -- every DB op below
    goes through `Store`."""
    # load_vec needs the raw conn to load the sqlite-vec extension;
    # Store.commit() exists for transaction control, but this needs low-level access.
    if not load_vec(store.conn):
        logger.warning("sqlite-vec unavailable; embeddings skipped")
        return 0, store.chunk_count()

    stored_model = store.get_meta("embed_model")
    if stored_model is not None and stored_model != llm.cfg.embed_model:
        logger.warning("embed model changed (%s -> %s): re-embedding all",
                       stored_model, llm.cfg.embed_model)
        store.clear_vectors()

    missing = store.pending_chunks()
    if not missing:
        return 0, 0
    try:
        vectors = llm.embed([_embed_input(row) for row in missing])
    except Exception as exc:
        logger.warning("embedding failed, %d chunks pending: %s",
                       len(missing), exc)
        return 0, len(missing)

    # An HTTP-200 response is not a guarantee the payload is well-formed:
    # a misconfigured/misbehaving endpoint can return an empty or short
    # `data` array. Without this check, `vectors[0]` below can IndexError
    # straight out of index_collection (empty `vectors`), or `zip` silently
    # truncates and over-reports `embedded` (short `vectors`). Treat
    # either as a pending-retry case, exactly like a request failure.
    if not vectors or len(vectors) != len(missing):
        logger.warning(
            "embedding response malformed (%d texts sent, %d vectors "
            "returned), %d chunks pending", len(missing), len(vectors),
            len(missing))
        return 0, len(missing)

    try:
        dim = len(vectors[0])
        stored_dim = store.get_meta("embed_dim")
        # The configured model *name* can stay the same while the vectors
        # it returns silently change shape underneath (backend swap behind
        # an alias, provider upgrade, etc). Without this check, an existing
        # chunk_vectors table just rejects the mismatched-dim insert below,
        # every chunk in `missing` falls back to pending, and the retry on
        # the next run hits the exact same mismatch forever -- permanently
        # wedged. Treat it exactly like a model change: drop the table and
        # meta, then fall through to recreate at the new dimension. Any
        # chunks with vectors under the old table (not in this `missing`
        # batch) are gone too; they'll show up as missing on the *next*
        # sync_vectors call, once they're absent from the fresh table.
        if (store.vector_table_exists() and stored_dim is not None
                and int(stored_dim) != dim):
            logger.warning(
                "embed dimension changed (%s -> %d) for model %s: "
                "re-embedding all", stored_dim, dim, llm.cfg.embed_model)
            store.clear_vectors()
        # Meta writes MUST happen before the insert loop below, not after:
        # a mid-batch dimension-mismatched vector (HTTP-200-but-malformed
        # payload -- this function's own documented threat model, see the
        # malformed-response check above) makes store_vectors's insert
        # raise partway through, straight into the `except` below, which
        # returns without touching either meta key. If embed_dim/
        # embed_model were written AFTER that insert loop, that raise
        # would skip them -- and since the table create (and any inserts
        # before the bad row) already happened and `index_collection`
        # commits unconditionally on return, the collection would end up
        # with a real chunk_vectors table at this dimension but embed_dim
        # permanently unset, and the dimension-drift guard above (which
        # only fires when `stored_dim is not None`) could never arm again
        # for this collection. Writing the metas first means they land in
        # the same commit as whatever the insert loop manages before
        # failing, exactly like the original (pre-Store) code, which set
        # both metas before its own insert loop for the same reason.
        newly_created = not store.vector_table_exists()
        if newly_created:
            store.set_meta("embed_dim", str(dim))
        store.set_meta("embed_model", llm.cfg.embed_model)
        store.store_vectors(
            [(row["rowid"], vector) for row, vector in zip(missing, vectors)],
            dim)
    except Exception as exc:
        logger.warning("writing embeddings failed, %d chunks pending: %s",
                       len(missing), exc)
        return 0, len(missing)
    return len(missing), 0


def format_index_counts(name: str, counts: dict, embedded: bool) -> str:
    """Format CLI indexing counts, including embeddings when configured."""
    line = (f"{name}: indexed {counts['indexed']}, "
            f"unchanged {counts['unchanged']}, "
            f"removed {counts['removed']}")
    if embedded:
        line += (f", embedded {counts['embedded']}, "
                 f"pending {counts['pending']}")
    if counts.get("chunk_warnings"):
        line += f", chunk-warnings {counts['chunk_warnings']}"
    return line


def index_collection(path: Path, embed=None, name: str | None = None,
                     kind: str = "docs", db_dir: Path | None = None) -> dict:
    """`name`: the collection name warnings are printed under (see
    `_emit_doc_warnings`) -- matches `Config`'s own bare-dir convention
    (`Collection(name=target.name, ...)`, `rag.config.load_config`) when
    omitted, so a direct `index_collection(path)` call (every test/caller
    that predates the chunk-contract warnings) still gets a sensible
    label with no argument changes required. `rag.cli` passes the real
    configured collection name explicitly.

    `kind`: `Collection.kind` (`rag.config`) -- `"docs"`, `"code"`, or
    `"memory"`. Defaults to `"docs"` so every pre-existing caller/test
    keeps warning. Chunk-contract warnings (`_emit_doc_warnings`, the
    `chunk_warnings` count, and `rag.cli`'s stdout suffix) only make
    sense for authored docs: a code/memory collection is GENERATED --
    generated source documentation, agent memory dumps -- with no author on the
    other end to fix an oversized section or a duplicate heading. Gated
    here (once, per collection) rather than per-doc: `kind` doesn't vary
    within a collection.

    `db_dir`: `Collection.db_dir` (`rag.config`) -- relocatable databases
    (2026-08-05). None (default) keeps today's in-folder behavior
    (`<path>/.docs-wiki/index.db`); given, the database lives at
    `<db_dir>/index.db` instead, entirely outside `path`. Threaded
    straight into `Store.open` -- nothing else in this function's walk of
    `path` needs to change, since content is still read from `path`
    either way."""
    store = Store.open(path, db_dir)
    collection = name if name is not None else path.name
    found: dict[str, str] = {}
    for md_file in sorted(path.rglob("*.md")):
        rel = md_file.relative_to(path)
        if rel.parts[0] == ".docs-wiki":
            continue
        found[str(rel)] = md_file.read_text(encoding="utf-8")

    existing = store.content_hashes()
    indexed = unchanged = chunk_warnings = 0
    for rel, text in found.items():
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if existing.get(rel) == digest:
            unchanged += 1
            continue
        meta, body = parse_front_matter(text)
        # Called directly (not via split_sections) so `doc.headings` is
        # also on hand for the duplicate-heading warning check below --
        # see module docstring. Still exactly one parse of this doc.
        doc = parse_structure(body)
        quadrant = quadrant_for(Path(rel))
        store.upsert_doc(rel, doc.title or Path(rel).stem, quadrant, meta,
                         digest, render_html(body), body)
        chunks, split_facts = _chunk_sections_with_facts(doc.sections)
        store.replace_chunks(rel, chunks)
        if kind == "docs":
            chunk_warnings += _emit_doc_warnings(collection, rel, doc,
                                                 split_facts)
        indexed += 1

    removed = store.remove_docs_not_in(found.keys())

    embedded = pending = 0
    if embed is not None:
        embedded, pending = sync_vectors(store, embed)

    store.commit()
    store.close()
    result = {"indexed": indexed, "unchanged": unchanged, "removed": removed,
              "embedded": embedded, "pending": pending}
    # Additive-only: a clean pass (the overwhelming common case, and every
    # pre-chunk-contract caller/test) gets back exactly the same 5-key
    # dict as before -- `chunk_warnings` only appears when there is one
    # to report, mirroring the stdout suffix rule in `rag.cli` (see
    # binding constraint: "existing format otherwise byte-identical").
    if chunk_warnings:
        result["chunk_warnings"] = chunk_warnings
    return result
