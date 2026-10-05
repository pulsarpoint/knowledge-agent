"""Section chunking and the chunk-contract check.

`chunk_sections` splits parsed sections (`rag.structure.parse_structure`)
into chunks no longer than a cap, at fence-aware paragraph boundaries. The
workspace worker (`rag.workspace_worker`) uses it for every Markdown file.

`check_folder` backs `rag index <docs-folder>`: it reads every Markdown file
under a folder and prints the three chunk-contract warning classes
(section-split, heading-less doc, duplicate heading text) to stderr. It writes
nothing; the app-docs plugin uses it as an authoring feedback loop.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import yaml

from rag.structure import DocStructure, Section, parse_structure

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_front_matter(text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[match.end():]


# The chunker's cap/overlap: a section longer than CAP gets split;
# OVERLAP is the character budget the old hard-slice fallback slides
# back by between consecutive slices (see `_hard_slice`). Named so
# the workspace worker and `chunk_sections`'s own default parameters can't
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
    Kept as a convenience wrapper -- `check_folder` calls `parse_structure`
    directly so it can also reach `doc.headings`.
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
    section-split warning facts `check_folder` needs: one
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
    a Markdown renderer's slug-dedupe walk consumes: a repeat anywhere in that
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


def check_folder(path: Path, name: str | None = None) -> tuple[int, int]:
    """Print chunk-contract warnings for every Markdown file under `path`
    (skipping a legacy `.docs-wiki/` state folder) and return
    `(documents checked, warnings printed)`. `name` labels the warnings; it
    defaults to the folder name. Warn, never fail: nothing is written."""
    collection = name if name is not None else path.name
    documents = warnings = 0
    for md_file in sorted(path.rglob("*.md")):
        rel = md_file.relative_to(path)
        if rel.parts[0] == ".docs-wiki":
            continue
        _meta, body = parse_front_matter(md_file.read_text(encoding="utf-8"))
        doc = parse_structure(body)
        _chunks, split_facts = _chunk_sections_with_facts(doc.sections)
        warnings += _emit_doc_warnings(collection, str(rel), doc, split_facts)
        documents += 1
    return documents, warnings
