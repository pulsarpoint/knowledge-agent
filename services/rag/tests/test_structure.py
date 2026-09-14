"""Single-parse markdown structure: ONE `md.parse(body)` feeds both the
chunker's section boundaries (`ingest.split_sections`) and the heading
sequence anchors are computed from (`anchors._rendered_ids`). These tests
pin `structure.parse_structure`'s own contract in isolation -- heading
extraction (level/text/line) and section-splitting semantics -- before
`ingest`/`anchors` are rewired onto it.

Several cases here are the direct payoff of dropping the hand-rolled
`_FENCE_RE` + H1/H2/H3 regex scanner for a real parser: a heading-lookalike
inside a fence or an indented code block is ignored not because we wrote a
special case for it, but because the CommonMark parser never emits a
heading token for it in the first place. The widened-fence case is the
exact "B2 bug class" the old scanner needed a hand-written comment and a
careful `len(delim) >= len(fence_delim)` rule to get right; the parser gets
it right for free.
"""
from rag import structure


def test_atx_heading_levels_text_and_lines():
    body = ("# Title\n\nIntro.\n\n## Section A\n\nBody A.\n\n"
            "### Sub A1\n\nBody sub.\n")
    result = structure.parse_structure(body)
    assert [(h.level, h.text, h.line) for h in result.headings] == [
        (1, "Title", 0),
        (2, "Section A", 4),
        (3, "Sub A1", 8),
    ]


def test_title_is_first_h1_or_empty_string():
    assert structure.parse_structure("## No H1 here\n\nBody.\n").title == ""
    assert structure.parse_structure("# One\n\nBody.\n\n# Two\n").title == "One"


def test_fence_heading_lookalike_is_not_a_heading():
    # The parser proves it: a `# fake` line inside a fenced code block is
    # fence content, never a heading token -- no fence-tracking needed here.
    body = "## Real\n\n```\n# fake\n```\n\n## Next\n\nBody.\n"
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]


def test_mixed_fence_delimiters_do_not_close_each_other():
    body = ("## Real\n\n```python\n~~~\n## fake heading\n~~~\n```\n\n"
            "## Next\n\nB.\n")
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]


def test_widened_fence_embedded_triple_backtick_is_not_a_heading():
    # CommonMark: a fence only closes on a line whose delimiter run is the
    # SAME character and AT LEAST AS LONG as the one that opened it. A
    # widened ```` fence safely contains an embedded ``` run (e.g. a
    # raw-string literal holding markdown) -- the old prefix-only
    # `_FENCE_RE` scanner needed a hand-written rule for this (the B2 bug
    # class); the real parser gets it right with no special-casing.
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
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]


def test_indented_code_block_heading_lookalike_is_not_a_heading():
    # 4-space indentation makes this an indented code block under
    # CommonMark, not a heading -- another case the old column-0 regex
    # scanner happened to get right by accident (it only matched "^# "),
    # but which a real parser gets right on purpose.
    body = "## Real\n\n    # not a heading, this is indented code\n\n## Next\n\nBody.\n"
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]


def test_setext_headings_are_recognized_as_h1_and_h2():
    # New capability vs. the old scanner (which only understood ATX):
    # setext underlines now count. This is the parser being right, not
    # drift -- documented in README.md's changelog paragraph.
    body = ("Title Text\n==========\n\nIntro.\n\n"
            "Section Text\n------------\n\nBody.\n")
    result = structure.parse_structure(body)
    assert (result.headings[0].level, result.headings[0].text) == (1, "Title Text")
    assert (result.headings[1].level, result.headings[1].text) == (2, "Section Text")
    assert result.title == "Title Text"


def test_intro_before_first_h2_and_h1_is_not_a_section_break():
    body = "# Title\n\nIntro para.\n\n## First\n\nBody.\n"
    result = structure.parse_structure(body)
    paths = [s.heading_path for s in result.sections]
    assert paths == ["(intro)", "First"]
    assert result.sections[0].text == "Intro para."
    # The H1 line itself never leaks into a section's text.
    assert "Title" not in result.sections[0].text


def test_h3_opens_h2_gt_h3_and_resets_on_next_h2():
    body = "## A\n\nBody A.\n\n### A1\n\nSub A1.\n\n## B\n\nBody B.\n"
    result = structure.parse_structure(body)
    paths = [s.heading_path for s in result.sections]
    assert paths == ["A", "A > A1", "B"]


def test_h4_is_not_a_section_break_and_its_line_stays_in_text():
    body = ("## First\n\nIntro.\n\n#### Note\n\nMore text.\n\n"
            "## Second\n\nBody.\n")
    result = structure.parse_structure(body)
    paths = [s.heading_path for s in result.sections]
    assert paths == ["First", "Second"]
    assert "#### Note" in result.sections[0].text
    assert "More text." in result.sections[0].text


def test_empty_sections_are_skipped():
    body = "## Empty\n\n## Real\n\nBody.\n"
    result = structure.parse_structure(body)
    assert [s.heading_path for s in result.sections] == ["Real"]


def test_heading_lines_are_excluded_from_section_text():
    body = "## Section\n\nBody line.\n"
    result = structure.parse_structure(body)
    assert result.sections == [structure.Section(heading_path="Section",
                                                  text="Body line.")]


def test_blockquote_nested_heading_is_not_a_section_break(): # CRITICAL-1 repro
    # A `> ## x` heading nested inside a blockquote is a REAL heading
    # (render_html assigns it an id, so it must stay in `headings` and
    # stay `is_root=False`) but must never break a section or delete its
    # own source line from the buffer -- the review found the line
    # actually vanishing ("the 'Real' section vanished entirely").
    body = "## Real\n\n> ## Nested\n> not a break.\n\nBody real.\n\n## Next\n\nBody next.\n"
    result = structure.parse_structure(body)
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]
    assert "> ## Nested" in result.sections[0].text
    assert "> not a break." in result.sections[0].text
    assert "Body real." in result.sections[0].text
    nested = next(h for h in result.headings if h.text == "Nested")
    assert (nested.level, nested.is_root) == (2, False)


def test_list_marker_nested_heading_is_not_a_section_break(): # CRITICAL-1 repro
    body = "## Real\n\n- ## Nested\n  more text\n\nBody real.\n\n## Next\n\nBody next.\n"
    result = structure.parse_structure(body)
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]
    assert "- ## Nested" in result.sections[0].text
    nested = next(h for h in result.headings if h.text == "Nested")
    assert (nested.level, nested.is_root) == (2, False)


def test_indented_continuation_heading_inside_list_item_is_not_a_break(): # CRITICAL-1 repro
    # A heading on an indented continuation line inside a list item (not
    # on the marker's own line) is still inside the list_item container.
    body = (
        "## Real\n\n"
        "- item text\n\n"
        "  ## Nested indented\n"
        "  more\n\n"
        "Body real.\n\n"
        "## Next\n\nBody next.\n"
    )
    result = structure.parse_structure(body)
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]
    nested = next(h for h in result.headings if h.text == "Nested indented")
    assert nested.is_root is False


def test_crlf_line_endings_do_not_shift_section_boundaries(): # CRITICAL-2 repro
    # token.map counts lines by "\n" only (after normalizing "\r\n"/"\r"
    # to "\n"); splitting the raw body on CRLF via str.splitlines() agrees
    # by coincidence for plain CRLF text, but the two disagree the moment
    # any of \v \f \x1c-\x1e \x85 shows up (next test) -- pin CRLF here as
    # the baseline "obviously must still work" case.
    body = "## Real\r\n\r\nBody real.\r\n\r\n## Next\r\n\r\nBody next.\r\n"
    result = structure.parse_structure(body)
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]
    assert result.sections[0].text == "Body real."
    assert result.sections[1].text == "Body next."


def test_line_separator_lookalike_character_does_not_shift_heading_lines(): # CRITICAL-2 repro
    # str.splitlines() treats \x0b (vertical tab) as a line break;
    # markdown_it's own line counting does not. One such character used to
    # shift every later line index out of sync with token.map -- the
    # review's repro: raw "## " markup leaking into indexed chunk text.
    body = "## Real\n\nBody with a \x0bvtab character inline.\n\n## Next\n\nBody next.\n"
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]
    assert result.sections[0].text == "Body with a \x0bvtab character inline."
    assert result.sections[1].text == "Body next."
    # the failure mode being guarded against: "## Next" leaking as literal
    # text into the wrong section instead of being recognized as a heading
    assert "## Next" not in result.sections[0].text


def test_break_walk_skips_stale_entry_without_stalling_later_breaks(): # CRITICAL-2 repro
    # Direct unit test of the resilience guard on the section-splitting
    # helper: a stale/duplicate break entry (start line already behind the
    # walk) must be skipped, never permanently wedge `break_idx` and
    # silently swallow every real break for the rest of the document.
    # Without the `while` skip, this exact input regresses to a single
    # "(intro)" section containing the literal "## B" line as text, with
    # the second heading never recognized as a break.
    lines = ["## A", "", "Body A.", "", "## B", "", "Body B.", ""]
    breaks = [(0, 1, 2, "A"), (0, 1, 2, "A-stale-duplicate"), (4, 5, 2, "B")]
    sections = structure._split_into_sections(lines, breaks)
    assert [s.heading_path for s in sections] == ["A", "B"]
    assert sections[0].text == "Body A."
    assert sections[1].text == "Body B."


def test_empty_atx_heading_text_section_is_not_mislabeled_as_intro(): # MINOR
    # "## " with nothing after it is a real (if oddly titled) empty-text
    # H2 -- its section must be keyed by "", never collapsed into the
    # pre-first-heading "(intro)" label.
    body = "## \n\nBody under an empty-titled heading.\n\n## Real\n\nBody real.\n"
    result = structure.parse_structure(body)
    paths = [s.heading_path for s in result.sections]
    assert paths == ["", "Real"]
    assert paths[0] != "(intro)"


def test_orphan_h3_before_any_h2_does_not_collapse_into_intro(): # regression guard
    # A malformed doc (H3 with no preceding H2) must keep the old
    # scanner's exact odd-but-distinct " > <h3>" label, not silently fold
    # into "(intro)" -- a regression the empty-heading fix above could
    # have introduced if current_h2 is None were checked in isolation.
    body = "### Orphan\n\nBody.\n"
    result = structure.parse_structure(body)
    assert result.sections == [structure.Section(heading_path=" > Orphan",
                                                  text="Body.")]


def test_trailing_hash_closing_sequence_is_stripped_from_heading_text(): # delta class
    result = structure.parse_structure("## Foo ##\n\nBody.\n")
    assert result.headings[0].text == "Foo"
    assert result.sections[0].heading_path == "Foo"


def test_tab_after_hashes_is_recognized_as_atx_heading(): # delta class
    result = structure.parse_structure("##\tTabbed\n\nBody.\n")
    assert result.headings[0].text == "Tabbed"


def test_one_to_three_space_indented_atx_is_recognized_as_heading(): # delta class
    result = structure.parse_structure("   ## Indented\n\nBody.\n")
    assert result.headings[0].text == "Indented"
    assert result.sections[0].heading_path == "Indented"


def test_html_block_heading_lookalike_is_not_a_heading(): # delta class
    # A `## ` line inside a raw HTML block is HTML content, not a heading
    # -- the old scanner had no concept of HTML blocks (only fences), so
    # this line would have been misread as a real section break.
    body = "## Real\n\n<div>\n## not a heading\n</div>\n\n## Next\n\nB.\n"
    result = structure.parse_structure(body)
    assert [h.text for h in result.headings] == ["Real", "Next"]
    assert [s.heading_path for s in result.sections] == ["Real", "Next"]


def test_section_semantics_match_old_chunker_on_canonical_doc():
    # Cross-check against the exact fixture test_chunker.py's
    # test_split_sections_titles_and_fence_awareness pins for the old
    # regex scanner -- same input, same expected boundaries, engine swapped
    # underneath.
    body = (
        "# Payment service\n\n"
        "Intro paragraph.\n\n"
        "## Endpoints\n\n"
        "POST /refunds does things.\n\n"
        "```text\n"
        "## not a heading (inside fence)\n"
        "```\n\n"
        "### Refund flow\n\n"
        "Details here.\n\n"
        "## Configuration\n\n"
        "KEY=value list.\n"
    )
    result = structure.parse_structure(body)
    assert result.title == "Payment service"
    paths = [s.heading_path for s in result.sections]
    assert "Endpoints" in paths
    assert "Endpoints > Refund flow" in paths
    assert "Configuration" in paths
    assert not any("not a heading" in p for p in paths)
