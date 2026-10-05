from pathlib import Path

from rag import chunking
from rag.structure import Section

DOC = """---
analyzed_commits:
  billing: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
updated: 2026-08-02
---
# Payment service

Intro paragraph.

## Endpoints

POST /refunds does things.

```text
## not a heading (inside fence)
```

### Refund flow

Details here.

## Configuration

KEY=value list.
"""


def test_front_matter_parsed():
    meta, body = chunking.parse_front_matter(DOC)
    assert meta["analyzed_commits"]["billing"].startswith("a")
    assert str(meta["updated"]) == "2026-08-02"
    assert body.lstrip().startswith("# Payment service")


def test_no_front_matter_is_empty_meta():
    meta, body = chunking.parse_front_matter("# Just a title\n")
    assert meta == {}
    assert body.startswith("# Just a title")


def test_split_sections_titles_and_fence_awareness():
    _, body = chunking.parse_front_matter(DOC)
    title, sections = chunking.split_sections(body)
    assert title == "Payment service"
    paths = [s.heading_path for s in sections]
    assert "Endpoints" in paths
    assert "Endpoints > Refund flow" in paths
    assert "Configuration" in paths
    assert not any("not a heading" in p for p in paths)


def test_chunk_oversize_section_overlaps():
    # No blank line anywhere in this text -- zero usable paragraph
    # boundaries -- so this is itself a hard-slice-fallback case (see
    # test_giant_single_paragraph_hard_slices for a more direct one);
    # pins the exact old sliding-window overlap semantics still used by
    # that fallback.
    big = Section(heading_path="Big", text="para. " * 1000)
    chunks = chunking.chunk_sections([big], cap=500, overlap=100)
    assert len(chunks) > 1
    assert all(c.heading_path == "Big" for c in chunks)
    assert chunks[1].text[:100] == chunks[0].text[-100:]  # real overlap
    assert all(len(c.text) <= 500 for c in chunks)


def test_two_paragraphs_straddling_cap_split_at_boundary():
    # p1 alone and p2 alone both fit under cap; only their combination
    # (302 chars) exceeds it (300) -- the split must land exactly at the
    # blank-line boundary between them, not mid-paragraph.
    p1, p2 = "A" * 150, "B" * 150
    section = Section(heading_path="S", text=f"{p1}\n\n{p2}")
    chunks = chunking.chunk_sections([section], cap=300, overlap=50)
    assert [c.text for c in chunks] == [p1, p2]
    assert all(c.heading_path == "S" for c in chunks)
    assert all(len(c.text) <= 300 for c in chunks)


def test_giant_single_paragraph_hard_slices():
    # One paragraph (no blank line inside it at all) that alone exceeds
    # cap: paragraph-boundary splitting has nothing to work with, so this
    # must fall back to the old hard-slice-with-overlap algorithm.
    big_para = "x" * 1000
    section = Section(heading_path="S", text=big_para)
    chunks = chunking.chunk_sections([section], cap=300, overlap=50)
    assert len(chunks) > 1
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[1].text[:50] == chunks[0].text[-50:]  # hard-slice overlap


def test_paragraph_split_carries_tail_paragraph_as_overlap():
    # p1+p2 fits under cap; p1+p2+p3 doesn't -- closes chunk 1 as
    # "p1\n\np2". Chunk 2 must carry p2 (chunk 1's last paragraph)
    # forward as the overlap unit, becoming "p2\n\np3" (which itself
    # fits under cap) -- p2's text appears in BOTH resulting chunks.
    p1, p2, p3 = "A" * 150, "B" * 150, "C" * 150
    section = Section(heading_path="S", text=f"{p1}\n\n{p2}\n\n{p3}")
    chunks = chunking.chunk_sections([section], cap=350, overlap=100)
    assert [c.text for c in chunks] == [f"{p1}\n\n{p2}", f"{p2}\n\n{p3}"]
    assert all(len(c.text) <= 350 for c in chunks)


def test_paragraph_that_alone_exceeds_cap_hard_slices_mid_stream():
    # A normal-sized paragraph, then one that alone exceeds cap, then
    # another normal one: the oversized paragraph hard-slices on its own
    # (old sliding-window algorithm) without disturbing paragraph-
    # boundary packing before/after it -- no tail-carry blending the two
    # mechanisms together.
    p1 = "A" * 100
    huge = "H" * 1000
    p3 = "C" * 100
    section = Section(heading_path="S", text=f"{p1}\n\n{huge}\n\n{p3}")
    chunks = chunking.chunk_sections([section], cap=300, overlap=50)
    assert chunks[0].text == p1
    assert chunks[-1].text == p3
    middle = chunks[1:-1]
    assert len(middle) > 1  # huge (1000 chars) needed more than one slice
    assert middle[1].text[:50] == middle[0].text[-50:]  # hard-slice overlap
    assert all(len(c.text) <= 300 for c in chunks)


def test_fence_internal_blank_line_is_not_a_chunk_boundary():
    # Review fix-wave (Important 2): manually traced -- with the OLD
    # (fence-blind) paragraph splitter, cap=32 forces the packer to close
    # a chunk right after "```\nXXXXXXXXXX" (only the OPENING fence
    # delimiter) and open the next one at "YYYYYYYYYY\n```" (only the
    # CLOSING one) -- a real mid-fence split, dangling delimiter in both
    # resulting chunks. Fence-aware splitting must never do that: the
    # fence's internal blank line (between the X's and Y's) is not a
    # candidate boundary at all, so the fence can only ever appear whole,
    # in exactly one chunk.
    p1 = "A" * 10
    fence = "```\nXXXXXXXXXX\n\nYYYYYYYYYY\n```"
    p2 = "B" * 10
    text = f"{p1}\n\n{fence}\n\n{p2}"
    section = Section(heading_path="S", text=text)
    chunks = chunking.chunk_sections([section], cap=32, overlap=5)
    assert all(c.text.count("```") % 2 == 0 for c in chunks)  # never dangling
    assert sum(fence in c.text for c in chunks) == 1  # intact, exactly once


def test_only_blank_line_inside_fence_falls_back_to_hard_slice():
    # The section's ONLY blank line sits between two halves of a single
    # fenced code block -- fence-aware paragraph splitting correctly
    # refuses to treat it as a boundary, merging the whole section into
    # ONE paragraph that (still oversized) falls through to the old
    # hard-slice-with-overlap fallback -- the documented "no safe
    # boundary exists" case (never fail; an unsafe/mid-fence split from
    # hard-slicing here is accepted and disclosed, see
    # `_fence_aware_paragraphs`'s docstring).
    fence_text = "```go\n" + ("x = 1\n" * 100) + "\n" + ("y = 2\n" * 100) + "```"
    section = Section(heading_path="S", text=fence_text)
    chunks = chunking.chunk_sections([section], cap=1000, overlap=100)
    assert len(chunks) > 1
    assert all(len(c.text) <= 1000 for c in chunks)
    assert chunks[1].text[:100] == chunks[0].text[-100:]  # hard-slice overlap


def test_widened_fence_cascade_does_not_suppress_later_paragraph_boundaries():
    # Review fix-wave round 2: a first-cut fence tracker used same-prefix
    # PARITY counting (any line starting with ``` or ~~~ toggles "inside
    # a fence", full stop) instead of real CommonMark character+run-
    # length matching. That desyncs on a WIDENED fence -- a
    # CommonMark-valid (and generated Markdown)
    # idiom where the opener is deliberately made longer than any
    # backtick run inside the content, specifically so an embedded
    # literal ``` line can never prematurely close it. Under parity
    # counting, that inner ``` line was WRONGLY counted as a second
    # delimiter -- and because the desynced state is a simple flag with
    # no memory of the real opener, it stays flipped ("inside a fence")
    # for the rest of the text, silently merging every later paragraph
    # into one blob, well past the fence's REAL close. Reproduced here
    # verbatim per the reviewer's repro: the correct fix must resolve
    # the fence at its real close and use every safe boundary after it.
    text = (
        "````\n"
        "some code\n"
        "```\n"          # narrower run, same char -- fence CONTENT, not a close
        "more code\n"
        "````\n"          # real close: same char, run length >= opener's
        "\n"
        "Paragraph A.\n"
        "\n"
        "Paragraph B.\n"
        "\n"
        "Paragraph C.\n"
    )
    assert chunking._fence_aware_paragraphs(text) == [
        "````\nsome code\n```\nmore code\n````",
        "Paragraph A.",
        "Paragraph B.",
        "Paragraph C.",
    ]


def test_widened_fence_end_to_end_chunking_keeps_paragraphs_intact():
    # Same shape as the cascade repro above, but exercised through the
    # full public chunk_sections path with a cap that forces real
    # packing: under the old parity-count bug this collapses to ONE
    # blob that then hard-slices at arbitrary character offsets --
    # verified directly against that pre-fix code, B lands split across
    # a slice boundary and never appears intact in any chunk. The fixed
    # version must keep every paragraph -- including the fence -- whole.
    fence = "````\nXXXXXXXXXX\n```\nYYYYYYYYYY\n````"
    a, b, c = "A" * 60, "B" * 60, "C" * 60
    text = f"{fence}\n\n{a}\n\n{b}\n\n{c}"
    section = Section(heading_path="S", text=text)
    chunks = chunking.chunk_sections([section], cap=150, overlap=20)
    assert any(fence in ch.text for ch in chunks)
    assert any(a in ch.text for ch in chunks)
    assert any(b in ch.text for ch in chunks)
    assert any(c in ch.text for ch in chunks)


def test_widened_fence_closes_on_equal_length_run():
    # CommonMark's closing rule is "same character, run length >= the
    # opener's" -- an EQUAL-length closer (not just a strictly longer
    # one) must close the fence.
    text = "````\ncode\n````\n\nAfter.\n"
    assert chunking._fence_aware_paragraphs(text) == ["````\ncode\n````", "After."]


def test_different_char_delimiter_inside_fence_is_content_not_close():
    # A ~~~ run inside a ``` fence is a different CHARACTER -- CommonMark
    # requires the closer match the opener's character, so this must
    # never close the fence, regardless of its run length.
    text = "```\ncode\n~~~\nmore code\n```\n\nAfter.\n"
    assert chunking._fence_aware_paragraphs(text) == [
        "```\ncode\n~~~\nmore code\n```", "After.",
    ]


def test_small_sections_pass_through():
    small = Section(heading_path="S", text="short text")
    assert chunking.chunk_sections([small]) == [small]


def test_mixed_fence_delimiters_do_not_close_each_other():
    body = "## Real\n\n```python\n~~~\n## fake heading\n~~~\n```\n\n## Next\n\nB.\n"
    _, sections = chunking.split_sections(body)
    paths = [s.heading_path for s in sections]
    assert "Real" in paths and "Next" in paths
    assert not any("fake heading" in p for p in paths)


def test_narrower_same_char_fence_line_does_not_close_widened_fence():
    # CommonMark: a fence only closes on a line whose delimiter run is the
    # SAME character and AT LEAST AS LONG as the one that opened it. A
    # widened ```` fence (opened to safely contain an embedded ``` run,
    # e.g. a raw-string literal holding markdown) must treat an inner ```
    # line as fence content, not a close -- the prefix-only regex this
    # pins against would have toggled fence state on that inner line,
    # producing a phantom heading and losing "## Next" to a stuck-open
    # fence state.
    body = (
        "## Real\n\n"
        "````text\n"
        "raw string starts here\n"
        "```\n"
        "## fake heading inside the raw string\n"
        "```\n"
        "````\n\n"
        "## Next\n\nB.\n"
    )
    _, sections = chunking.split_sections(body)
    paths = [s.heading_path for s in sections]
    assert "Real" in paths and "Next" in paths
    assert not any("fake heading" in p for p in paths)


def test_nested_container_heading_is_not_a_section_break():
    # A blockquoted `##` is a real heading but never a chunk section.
    body = (
        "## Real\n\n"
        "> ## Nested (blockquote, not a break)\n"
        "> more quoted text\n\n"
        "Body real.\n\n"
        "## Next\n\nBody next.\n"
    )
    _, sections = chunking.split_sections(body)
    paths = [s.heading_path for s in sections]
    assert paths == ["Real", "Next"]


def test_overlap_ge_cap_raises():
    import pytest
    with pytest.raises(ValueError):
        chunking.chunk_sections([Section("S", "x" * 10)], cap=100, overlap=100)
