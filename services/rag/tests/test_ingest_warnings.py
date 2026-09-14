"""Chunk-contract warnings: the three deterministic classes `index_collection`
prints to stderr while (re)indexing (`rag index: warning: ...`), the
per-collection `chunk_warnings` count they roll up into, and the CLI's
additive-only `, chunk-warnings N` stdout suffix. See
`docs/superpowers/plans/2026-08-04-chunk-contract.md` and
`ingest._emit_doc_warnings`'s docstring for the exact rules pinned here.
"""
from rag import cli, ingest


def write_doc(docs, rel, text):
    path = docs / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_clean_corpus_emits_no_warnings(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "a.md",
              "# Alpha\n\n## Usage\n\nRun the alpha refund pipeline.\n\n"
              "## Setup\n\nInstall the alpha tool.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert "chunk_warnings" not in counts
    assert capsys.readouterr().err == ""


def test_no_headings_warning_exact_format(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "Just body text, no headings at all.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: plain.md: no headings -- "
        "content not deep-linkable\n"
    )
    assert counts["chunk_warnings"] == 1


def test_h1_only_doc_is_not_heading_less(tmp_path, capsys):
    # A title (H1) with no H2/H3 still puts all body text under the
    # synthetic "(intro)" section, but the doc DOES have a heading (the
    # title) -- "heading-less" means literally zero headings, not "zero
    # chunk-splitting subsections". See ingest._emit_doc_warnings.
    docs = tmp_path / "docs"
    write_doc(docs, "titled.md", "# Home\n\nWelcome.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert capsys.readouterr().err == ""
    assert "chunk_warnings" not in counts


def test_duplicate_heading_warning_exact_format_and_count(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "dup.md",
              "# Title\n\n## Overview\n\nA.\n\n## Overview\n\nB.\n\n"
              "## Overview\n\nC.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: dup.md: duplicate heading "
        "'Overview' (x3) -- anchors suffixed\n"
    )
    assert counts["chunk_warnings"] == 1


def test_duplicate_heading_across_levels_and_containers_counted_together(
        tmp_path, capsys):
    # "anchors suffixed" is a claim about render_html's slug-dedupe walk,
    # which runs over EVERY heading (any level, root or nested inside a
    # blockquote/list) -- so must this warning's count.
    docs = tmp_path / "docs"
    write_doc(docs, "dup2.md",
              "# Title\n\n## Setup\n\n> ## Setup\n> quoted body\n\n"
              "Root body.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: dup2.md: duplicate heading "
        "'Setup' (x2) -- anchors suffixed\n"
    )
    assert counts["chunk_warnings"] == 1


def test_section_split_warning_exact_format(tmp_path, capsys):
    docs = tmp_path / "docs"
    content = "x" * 2500
    write_doc(docs, "big.md", f"# Title\n\n## Endpoints\n\n{content}\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: big.md > 'Endpoints': 2500 chars, "
        "split into 2 chunks\n"
    )
    assert counts["chunk_warnings"] == 1


def test_multiple_split_sections_each_get_their_own_warning_line(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    a, b = "a" * 2100, "b" * 2200
    write_doc(docs, "big2.md",
              f"# Title\n\n## First\n\n{a}\n\n## Second\n\n{b}\n")
    counts = ingest.index_collection(docs, name="mycol")
    err_lines = capsys.readouterr().err.splitlines()
    assert err_lines == [
        "rag index: warning: mycol: big2.md > 'First': 2100 chars, "
        "split into 2 chunks",
        "rag index: warning: mycol: big2.md > 'Second': 2200 chars, "
        "split into 2 chunks",
    ]
    assert counts["chunk_warnings"] == 2


def test_warnings_aggregate_across_docs_in_one_pass(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "clean.md", "# Clean\n\n## Body\n\nFine.\n")
    write_doc(docs, "plain.md", "No headings here at all.\n")
    write_doc(docs, "dup.md",
              "# T\n\n## X\n\nA.\n\n## X\n\nB.\n")
    counts = ingest.index_collection(docs, name="mycol")
    assert counts["chunk_warnings"] == 2
    err = capsys.readouterr().err
    assert err.count("rag index: warning:") == 2


def test_unchanged_doc_is_not_rewarned_on_reindex(tmp_path, capsys):
    # Only (re)indexed docs are parsed/warned this pass; an unchanged doc
    # is skipped entirely before its content is even re-read.
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    ingest.index_collection(docs, name="mycol")
    capsys.readouterr()  # drain the first pass's warning
    counts = ingest.index_collection(docs, name="mycol")
    assert counts["unchanged"] == 1
    assert "chunk_warnings" not in counts
    assert capsys.readouterr().err == ""


def test_index_collection_defaults_collection_name_to_path_name(
        tmp_path, capsys):
    docs = tmp_path / "my-docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    ingest.index_collection(docs)  # no name= passed
    assert "my-docs: plain.md:" in capsys.readouterr().err


def test_stdout_gains_chunk_warnings_suffix_only_when_positive(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    assert cli.main(["index", str(docs)]) == 0
    out = capsys.readouterr().out
    assert out == f"{docs.name}: indexed 1, unchanged 0, removed 0, " \
                  f"chunk-warnings 1\n"


def test_stdout_suffix_absent_and_byte_identical_when_clean(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "a.md", "# A\n\n## B\n\nBody.\n")
    assert cli.main(["index", str(docs)]) == 0
    out = capsys.readouterr().out
    assert out == f"{docs.name}: indexed 1, unchanged 0, removed 0\n"
    assert "chunk-warnings" not in out


def test_code_kind_collection_exempt_from_chunk_warnings(tmp_path, capsys):
    # code/memory collections are GENERATED (generated source documentation,
    # agent memory dumps) -- there is no author on the other end to feed
    # a chunk-contract warning back to, so `kind="code"` (and
    # `"memory"`) skips `_emit_doc_warnings` entirely: no stderr lines,
    # no `chunk_warnings` key, regardless of how badly a section
    # violates the cap.
    docs = tmp_path / "docs"
    content = "x" * 2500
    write_doc(docs, "big.md", f"# Title\n\n## Endpoints\n\n{content}\n")
    counts = ingest.index_collection(docs, name="mycol", kind="code")
    assert capsys.readouterr().err == ""
    assert "chunk_warnings" not in counts


def test_memory_kind_collection_exempt_from_chunk_warnings(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    counts = ingest.index_collection(docs, name="mycol", kind="memory")
    assert capsys.readouterr().err == ""
    assert "chunk_warnings" not in counts


def test_docs_kind_default_still_warns(tmp_path, capsys):
    # kind defaults to "docs" -- every pre-existing caller/test (which
    # never passes kind=) keeps warning with no argument changes.
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    counts = ingest.index_collection(docs, name="mycol", kind="docs")
    assert capsys.readouterr().err != ""
    assert counts["chunk_warnings"] == 1


def test_cli_index_omits_chunk_warnings_suffix_for_code_collection(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[[collections]]\nname = "mycol"\npath = "{docs}"\n'
        'kind = "code"\n', encoding="utf-8")
    assert cli.main(["index", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert out == "mycol: indexed 1, unchanged 0, removed 0\n"
    assert "chunk-warnings" not in out
