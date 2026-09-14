"""Heading anchors: map indexed heading paths to their rendered-page slugs.

Anchors are computed at read time from the indexed `chunks` table rather
than stored as a column (FTS5 virtual tables cannot gain columns via
ALTER TABLE) -- a documented deviation from the spec's "stored on chunks"
phrasing with an identical API contract. Moved here from docs-wiki's
`webapp._heading_anchors` + `api._headings` (Plan §7.1/Task 3) so both the
document-detail route and search-hit anchors share one implementation.
"""
from __future__ import annotations

import functools
import logging

from rag.content import _dedupe_slug, unique_slugs
from rag.store import Store
from rag.structure import parse_structure

logger = logging.getLogger("rag.anchors")


# `heading_anchors` is on the search/document-detail read path -- every hit
# in a search response calls it, and search responses cite the same handful
# of hot docs over and over. Without memoizing, each call re-runs a full
# markdown_it parse over that doc's whole body (measured ~54x slower than a
# cached lookup for a repeat call on the same doc -- see task fix report for
# the actual before/after numbers). Keyed on the markdown string itself
# (not the doc path): strings hash fine, and this way a change in content
# is automatically a cache miss with no separate invalidation to get wrong.
# Read-only callers only (`heading_anchors` below never mutates the
# returned dict), so returning the same cached object by reference is safe.
@functools.lru_cache(maxsize=256)
def _rendered_ids(markdown: str) -> dict[str, str]:
    """Map each chunk-style heading label ("H2" or "H2 > H3") to the id the
    renderer assigns that heading. The renderer (UI Markdown.tsx and
    render_html alike) runs the dedupe rule over EVERY heading in document
    order -- H1, H4+, empty sections, AND headings nested inside a
    blockquote/list item all consume the slug namespace -- so the walk
    here must too, even though only ROOT-level H2/H3 (`Heading.is_root`)
    produce chunk labels: a nested H2/H3 lookalike must never be mistaken
    for a real chunk-section heading, or this function and
    `ingest.split_sections` (which applies the identical root-only rule)
    would disagree about what "current H2/H3" means -- exactly the class
    of cross-consumer drift this whole single-parse refactor exists to
    kill. Heading order/text comes from the same single parse
    `split_sections` builds its section boundaries from
    (`structure.parse_structure`), so a `# comment` inside a code block
    never counts as a heading here for the same reason it never does
    there: one parser, one opinion. First occurrence wins for duplicate
    labels (chunks GROUP BY merges their text anyway)."""
    used: dict[str, int] = {}
    ids: dict[str, str] = {}
    current_h2 = current_h3 = ""
    for heading in parse_structure(markdown).headings:
        # Consumed unconditionally, root or not: render_html assigns an id
        # to EVERY heading_open token regardless of container nesting, so
        # the slug-dedupe counter must walk every heading in document
        # order to stay in lockstep with what the renderer actually did.
        slug = _dedupe_slug(used, heading.text)
        if not heading.is_root:
            continue
        if heading.level == 2:
            current_h2, current_h3 = heading.text, ""
        elif heading.level == 3:
            current_h3 = heading.text
        else:
            continue
        label = (f"{current_h2} > {current_h3}" if current_h3
                 else current_h2)
        ids.setdefault(label, slug)
    return ids


def heading_anchors(store: Store, doc_path: str) -> dict[str, str]:
    """Map each non-intro heading_path of `doc_path` to its rendered-page
    anchor id, from the indexed `docs.markdown` column, never the raw file
    (which may have vanished from disk since the last index run). Shared by
    the document-detail route's TOC and search hits' anchors so both always
    agree on where a heading actually lives."""
    markdown = store.get_markdown(doc_path)
    ids = _rendered_ids(markdown) if markdown is not None else {}
    labels = [heading_path for heading_path in store.chunk_heading_paths(doc_path)
              if heading_path != "(intro)"]
    if labels and not ids:
        # Legacy-migration state: the markdown backfill left '' for a file
        # that had vanished from disk, so rendered ids cannot be derived.
        # Fall back to the pre-fix label-derived slugs (collision-prone on
        # H1/H4/empty-section name clashes, but never None) and tell the
        # operator how to heal it.
        logger.warning(
            "no stored markdown for %s; anchors fall back to label-derived "
            "slugs -- reindex to restore exact anchors", doc_path)
        slugs = unique_slugs([label.split(" > ")[-1] for label in labels])
        return dict(zip(labels, slugs))
    return {label: ids.get(label) for label in labels}


def headings(store: Store, doc_path: str) -> list[dict]:
    """Ordered `{text, anchor, level}` entries for `doc_path`'s on-page
    TOC. `level` is derived from heading depth (h2/h3 only, since chunking
    only tracks up to h3): a bare h2 label has no " > " separator (level
    2), an "h2 > h3" label has one (level 3), capped at 3."""
    return [{"text": label.split(" > ")[-1],
             "anchor": anchor,
             "level": min(3, label.count(" > ") + 2)}
            for label, anchor in heading_anchors(store, doc_path).items()]
