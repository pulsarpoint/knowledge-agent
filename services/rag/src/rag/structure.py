"""Single-parse markdown structure.

ONE `markdown_it` parse of a document's body feeds both consumers that used
to run their own hand-rolled scanner over the same text: the chunker
(`ingest.split_sections`, which used a fence-delimiter-tracking regex plus
H1/H2/H3 line regexes) and the anchor computation
(`anchors._rendered_ids`, which had its own copy of that same fence loop).
Two independent regex-based re-implementations of "what counts as a
heading" is exactly the class of bug `test_catalog_anchor_parity.py` exists
to catch (a doc-comment body line that looked like a heading desynced one
scanner from the other). Parsing once here and handing both consumers the
same token-derived heading sequence makes that class of drift structurally
impossible: there is only one opinion of where the headings are, because
there is only one parse.

`Section` (chunk text keyed by its heading path) is defined here, not in
`ingest`, so both `ingest.split_sections` and `store.Store.replace_chunks`
can depend on it without either owning it.

This is NOT a byte-for-byte port of the old line scanner -- a real parser
is right about more things than a regex scanner was, and those deltas are
real, observable behavior changes for any document that hits them (see
`parse_structure`'s docstring for the enumerated list). The corpus golden
check (dgp-docs, 32/32 docs) came back byte-identical only because none of
those docs happen to contain a doc that exercises any delta class -- that
is a property of the corpus, not a guarantee of the implementation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

# Reused across calls: markdown_it's .parse() does not mutate instance
# state per call (it returns a fresh token list each time), so a shared
# instance is safe and avoids re-building the parser's rule chain on every
# document. Contrast with content.render_html, which still builds a fresh
# MarkdownIt per call -- that one's add_render_rule closures capture a
# call-local `used_slugs` dict, so *that* instance genuinely is call-scoped.
_MD = MarkdownIt("gfm-like")

# Token types that mean "we are inside a container block" for the purposes
# of section-break detection: a heading nested inside a blockquote or a
# list item (`> ## x`, `- ## x`, or a heading on an indented continuation
# line inside a list item) is still a REAL heading -- render_html assigns
# it a real id, consuming the same slug namespace as any other heading --
# but it must never be treated as a chunk-section boundary or change
# "current H2/H3" tracking, matching what the old column-0-anchored regex
# scanner did (it never matched an indented/prefixed line at all, so a
# nested heading was always inert body text to it). bullet_list_open /
# ordered_list_open don't need their own counters: a heading can only ever
# be a descendant of a list via an intervening list_item_open, never a
# direct child of the list token itself.
_CONTAINER_OPEN = frozenset({"blockquote_open", "list_item_open"})
_CONTAINER_CLOSE = frozenset({"blockquote_close", "list_item_close"})


@dataclass
class Section:
    heading_path: str
    text: str


@dataclass
class Heading:
    level: int
    text: str  # inline token content -- the rendered heading text, the
               # same source render_html's `id="..."` slugs are built from.
    line: int  # 0-indexed source line the heading starts on (token.map[0]).
    is_root: bool  # False for a heading nested inside a blockquote or list
               # item (container depth > 0 at the heading). Still a real
               # heading (still in this list, still consumes render_html's
               # slug namespace) -- just never a section break and never
               # "current H2/H3" state for a consumer that needs that
               # distinction (see `ingest.split_sections` and
               # `anchors._rendered_ids`, which must and do agree on this).


@dataclass
class DocStructure:
    title: str
    headings: list[Heading]
    sections: list[Section]


def parse_structure(body: str) -> DocStructure:
    """Parse `body` once and derive both the full heading sequence (every
    level, every container depth, document order -- what anchors needs to
    replicate the renderer's slug-dedupe walk) and the chunker's H2/H3
    section split (what `split_sections` needs), from the same token
    stream.

    Section-splitting semantics for ROOT-level (not inside a blockquote or
    list) ATX H1/H2/H3 headings match the old line scanner: content before
    the first root H2 becomes the "(intro)" section, a root H2 opens a new
    section, a root H3 opens "<h2> > <h3>" inside the current H2, heading
    lines themselves never appear in section text, and a section with no
    non-heading content is dropped. Root H1 lines are excluded from
    section text (like H2/H3) but never open or close a section -- only
    the first root H1 becomes `title`. Root H4+ heading lines are NOT
    recognized as breaks (matching the old scanner, which only had
    H1/H2/H3 regexes) and so fall straight through into whichever
    section's text they land in, hashes and all -- exactly like the old
    scanner's regex-miss-so-just-append-the-line fallthrough. An H2/H3
    with genuinely empty text (`## ` with nothing after it) still opens
    its own section, keyed by that empty string -- it is a real (if oddly
    titled) heading, not the absence of one, so it must never collapse
    into "(intro)".

    This is a real parser, not a regex scanner, so it is NOT a byte-for-
    byte port of the old scanner's behavior -- it is a strict superset of
    correctness, and every additional thing it gets right is an observable
    delta for a document that happens to exercise it:

    - Setext headings (`Text\\n===` / `Text\\n---`) are now recognized as
      H1/H2. The old scanner only matched ATX (`#`-prefixed) headings and
      silently treated setext syntax as body text.
    - A trailing closing hash run on an ATX heading (`## Foo ##`) is now
      stripped from the heading text/label (`"Foo"`, not `"Foo ##"`) --
      real CommonMark ATX closing-sequence handling, which the old
      `re.compile(r"^## (.+)$")` had no concept of (it captured the whole
      rest of the line verbatim).
    - A tab after the hashes (`##\\tFoo`), or 1-3 leading spaces before
      them (`   ## Foo`), now count as a valid ATX heading. The old
      scanner's regex required an exact `"^## "` (single literal space,
      column 0).
    - A heading-lookalike line inside a raw HTML block (e.g. a `## `
      line between `<div>` and `</div>`) is correctly NOT a heading. The
      old scanner tracked fenced-code state but had no concept of HTML
      blocks at all, so a `## ` line there would have been misread as a
      real section break.
    - A heading nested inside a blockquote or list item (`> ## x`,
      `- ## x`, or an indented continuation-line heading inside a list
      item) is real (appears in `headings`, gets a render_html id, and is
      resolvable by anchors) but is never a section break and never
      updates "current H2/H3" -- see `Heading.is_root`. The old scanner's
      column-0-anchored regexes never matched an indented/prefixed line
      either, so root-level chunk boundaries are unaffected either way;
      what changed is that anchors now correctly accounts for these
      headings consuming slug namespace (matching what render_html has
      always actually assigned them), where the old anchors scanner had
      the same blind spot and silently miscounted.

    None of dgp-docs' 32 files happen to exercise any of the above (see
    the task report's corpus check), which is why that check came back
    byte-identical -- that is a property of the corpus, not a claim that
    these deltas don't exist. See README.md's changelog paragraph.
    """
    tokens = _MD.parse(body)
    # markdown_it's `token.map` line numbers are computed by splitting the
    # input on "\n" only (after normalizing "\r\n"/"\r" to "\n" up front).
    # `str.splitlines()` additionally splits on \v \f \x1c-\x1e \x85 (NEL)
    #     -- a single occurrence of any of those in real content
    # would silently shift every line index after it out of sync with
    # `token.map`, corrupting every later section boundary (raw `## `
    # markup leaking into indexed chunk text, or worse). Matching
    # markdown_it's own normalization exactly is the only way to guarantee
    # `lines[i]` is the same line `token.map`'s `i` means.
    lines = re.sub(r"\r\n?", "\n", body).split("\n")

    headings: list[Heading] = []
    # (start_line, end_line, level, text) for ROOT-level H1/H2/H3 only --
    # the subset of headings that affect section splitting. end_line is
    # exclusive (token.map[1]); for a one-line ATX heading that's start+1,
    # for a two-line setext heading (text line + underline) it's start+2,
    # so both source lines of a setext heading are excluded from section
    # text.
    breaks: list[tuple[int, int, int, str]] = []
    container_depth = 0
    for idx, token in enumerate(tokens):
        if token.type in _CONTAINER_OPEN:
            container_depth += 1
            continue
        if token.type in _CONTAINER_CLOSE:
            container_depth -= 1
            continue
        if token.type != "heading_open":
            continue
        level = int(token.tag[1:])
        text = tokens[idx + 1].content
        start, end = token.map
        is_root = container_depth == 0
        headings.append(Heading(level=level, text=text, line=start, is_root=is_root))
        if is_root and level in (1, 2, 3):
            breaks.append((start, end, level, text))

    title = next((h.text for h in headings if h.level == 1 and h.is_root), "")
    sections = _split_into_sections(lines, breaks)

    return DocStructure(title=title, headings=headings, sections=sections)


def _split_into_sections(
    lines: list[str], breaks: list[tuple[int, int, int, str]]
) -> list[Section]:
    """Walk `lines` (already split on the same line boundaries `breaks`'
    start/end indices were computed against -- see `parse_structure`'s
    normalization) and cut it into `Section`s at each ROOT-level H1/H2/H3
    entry in `breaks` (`(start_line, end_line, level, text)`, `end_line`
    exclusive, sorted by `start_line` -- see `parse_structure` for how
    it's built). Split out as its own function so the break-walk's
    resilience (below) is directly unit-testable without needing a
    contrived token stream to produce a mismatched `breaks` list.
    """
    sections: list[Section] = []
    # None means "no root H2/H3 opened yet" -- distinct from "" (a real
    # root H2/H3 whose text happens to be empty), so an empty-titled
    # heading's section is never mistaken for the pre-first-heading intro.
    current_h2: str | None = None
    current_h3: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        if current_h2 is None and current_h3 is None:
            # Neither a root H2 nor a root H3 has opened yet: this is
            # genuinely pre-first-heading content.
            path = "(intro)"
        elif current_h3 is not None:
            # `current_h2 or ""` matters for a malformed doc where a root
            # H3 appears with no preceding root H2 -- current_h2 is still
            # None there, and the old scanner's heading_path for that case
            # was " > <h3 text>" (a section, however oddly labeled -- not
            # "(intro)"); this preserves that exact string.
            path = f"{current_h2 or ''} > {current_h3}"
        else:
            path = current_h2
        sections.append(Section(heading_path=path, text=text))

    line_idx = 0
    total_lines = len(lines)
    break_idx = 0
    while line_idx < total_lines:
        # Skip any break entries whose start line is already behind us.
        # This should not normally happen once `lines` and `token.map`
        # agree on line numbering (the whole point of the CRLF/line-
        # separator fix in `parse_structure`), but staying resilient here
        # means one unanticipated mismatch can only ever cost that ONE
        # break -- never permanently wedge break_idx behind a stale entry
        # and silently swallow every real section break for the rest of
        # the document (see test_break_walk_skips_stale_entry_... for the
        # exact failure mode this guards against).
        while break_idx < len(breaks) and breaks[break_idx][0] < line_idx:
            break_idx += 1
        if break_idx < len(breaks) and breaks[break_idx][0] == line_idx:
            start, end, level, text = breaks[break_idx]
            break_idx += 1
            if level == 2:
                flush()
                current_h2, current_h3 = text, None
            elif level == 3:
                flush()
                current_h3 = text
            # level == 1: excluded from text below via the line_idx jump,
            # but never flushes/opens a section.
            line_idx = max(end, line_idx + 1)
            continue
        buffer.append(lines[line_idx])
        line_idx += 1
    flush()

    return sections
