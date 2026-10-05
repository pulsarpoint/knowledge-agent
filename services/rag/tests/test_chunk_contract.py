"""Chunk-contract warnings: the three deterministic classes `check_folder`
prints to stderr (`rag index: warning: ...`), the count they roll up into,
and the CLI's `, chunk-warnings N` stdout suffix. See
`chunking._emit_doc_warnings`'s docstring for the exact rules pinned here.
"""
from rag import chunking, cli


def check(docs, name=None):
    _documents, warnings = chunking.check_folder(docs, name=name)
    return warnings


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
    warnings = check(docs, name="mycol")
    assert warnings == 0
    assert capsys.readouterr().err == ""


def test_no_headings_warning_exact_format(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "Just body text, no headings at all.\n")
    warnings = check(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: plain.md: no headings -- "
        "content not deep-linkable\n"
    )
    assert warnings == 1


def test_h1_only_doc_is_not_heading_less(tmp_path, capsys):
    # A title (H1) with no H2/H3 still puts all body text under the
    # synthetic "(intro)" section, but the doc DOES have a heading (the
    # title) -- "heading-less" means literally zero headings, not "zero
    # chunk-splitting subsections". See chunking._emit_doc_warnings.
    docs = tmp_path / "docs"
    write_doc(docs, "titled.md", "# Home\n\nWelcome.\n")
    warnings = check(docs, name="mycol")
    assert capsys.readouterr().err == ""
    assert warnings == 0


def test_duplicate_heading_warning_exact_format_and_count(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "dup.md",
              "# Title\n\n## Overview\n\nA.\n\n## Overview\n\nB.\n\n"
              "## Overview\n\nC.\n")
    warnings = check(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: dup.md: duplicate heading "
        "'Overview' (x3) -- anchors suffixed\n"
    )
    assert warnings == 1


def test_duplicate_heading_across_levels_and_containers_counted_together(
        tmp_path, capsys):
    # "anchors suffixed" is a claim about render_html's slug-dedupe walk,
    # which runs over EVERY heading (any level, root or nested inside a
    # blockquote/list) -- so must this warning's count.
    docs = tmp_path / "docs"
    write_doc(docs, "dup2.md",
              "# Title\n\n## Setup\n\n> ## Setup\n> quoted body\n\n"
              "Root body.\n")
    warnings = check(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: dup2.md: duplicate heading "
        "'Setup' (x2) -- anchors suffixed\n"
    )
    assert warnings == 1


def test_section_split_warning_exact_format(tmp_path, capsys):
    docs = tmp_path / "docs"
    content = "x" * 2500
    write_doc(docs, "big.md", f"# Title\n\n## Endpoints\n\n{content}\n")
    warnings = check(docs, name="mycol")
    assert capsys.readouterr().err == (
        "rag index: warning: mycol: big.md > 'Endpoints': 2500 chars, "
        "split into 2 chunks\n"
    )
    assert warnings == 1


def test_multiple_split_sections_each_get_their_own_warning_line(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    a, b = "a" * 2100, "b" * 2200
    write_doc(docs, "big2.md",
              f"# Title\n\n## First\n\n{a}\n\n## Second\n\n{b}\n")
    warnings = check(docs, name="mycol")
    err_lines = capsys.readouterr().err.splitlines()
    assert err_lines == [
        "rag index: warning: mycol: big2.md > 'First': 2100 chars, "
        "split into 2 chunks",
        "rag index: warning: mycol: big2.md > 'Second': 2200 chars, "
        "split into 2 chunks",
    ]
    assert warnings == 2


def test_warnings_aggregate_across_docs_in_one_pass(tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "clean.md", "# Clean\n\n## Body\n\nFine.\n")
    write_doc(docs, "plain.md", "No headings here at all.\n")
    write_doc(docs, "dup.md",
              "# T\n\n## X\n\nA.\n\n## X\n\nB.\n")
    warnings = check(docs, name="mycol")
    assert warnings == 2
    err = capsys.readouterr().err
    assert err.count("rag index: warning:") == 2


def test_check_folder_defaults_collection_name_to_path_name(
        tmp_path, capsys):
    docs = tmp_path / "my-docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    check(docs)  # no name= passed
    assert "my-docs: plain.md:" in capsys.readouterr().err


def test_stdout_gains_chunk_warnings_suffix_only_when_positive(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "plain.md", "No headings here at all.\n")
    assert cli.main(["index", str(docs)]) == 0
    out = capsys.readouterr().out
    assert out == f"{docs.name}: checked 1, chunk-warnings 1\n"


def test_stdout_suffix_absent_and_byte_identical_when_clean(
        tmp_path, capsys):
    docs = tmp_path / "docs"
    write_doc(docs, "a.md", "# A\n\n## B\n\nBody.\n")
    assert cli.main(["index", str(docs)]) == 0
    out = capsys.readouterr().out
    assert out == f"{docs.name}: checked 1\n"
    assert "chunk-warnings" not in out
