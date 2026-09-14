"""Tests for citation parsing and verification."""
import json
import pytest
from pathlib import Path

from code_agent.citations import parse_answer, verify, CITATIONS_MARKER


class TestParseAnswer:
    def test_delimiter_inside_source_quote_is_preserved(self):
        citations = [{'file': 'prompt.py', 'lines': '1', 'quote': 'Use CITATIONS: followed by JSON'}]
        text = 'Explanation.\nCITATIONS:\n' + json.dumps(citations)
        answer, parsed, not_found = parse_answer(text)
        assert answer == 'Explanation.'
        assert parsed == citations
        assert not_found is False

    def test_parse_answer_with_marker_and_array(self):
        """Test parsing answer with CITATIONS: marker and JSON array."""
        text = "The answer is here.\nCITATIONS:\n[{\"file\":\"a.py\",\"lines\":\"1-5\",\"quote\":\"test\"}]"
        answer, citations, not_found = parse_answer(text)
        assert answer == "The answer is here."
        assert citations == [{"file": "a.py", "lines": "1-5", "quote": "test"}]
        assert not_found is False

    def test_parse_answer_not_found(self):
        """Test parsing answer that starts with NOT_FOUND."""
        text = "NOT_FOUND: No matching files found."
        answer, citations, not_found = parse_answer(text)
        assert not_found is True
        assert answer == "NOT_FOUND: No matching files found."
        assert citations == []

    def test_parse_answer_no_marker(self):
        """Test parsing answer with no CITATIONS: marker."""
        text = "The answer without any citations."
        answer, citations, not_found = parse_answer(text)
        assert answer == "The answer without any citations."
        assert citations == []
        assert not_found is False

    def test_parse_answer_malformed_json_after_marker(self):
        """Test parsing with malformed JSON after marker."""
        text = "The answer here.\nCITATIONS:\n{invalid json}"
        answer, citations, not_found = parse_answer(text)
        assert answer == "The answer here."
        assert citations == []
        assert not_found is False

    def test_parse_answer_two_markers_last_wins(self):
        """Test that when multiple CITATIONS: markers exist, the last one is used."""
        text = "First part.\nCITATIONS:\n[{\"file\":\"a.py\",\"lines\":\"1\",\"quote\":\"old\"}]\nMore text.\nCITATIONS:\n[{\"file\":\"b.py\",\"lines\":\"2\",\"quote\":\"new\"}]"
        answer, citations, not_found = parse_answer(text)
        # Answer should be everything before the LAST marker (includes between-markers text)
        assert answer == "First part.\nCITATIONS:\n[{\"file\":\"a.py\",\"lines\":\"1\",\"quote\":\"old\"}]\nMore text."
        # Citations should be from the LAST marker
        assert citations == [{"file": "b.py", "lines": "2", "quote": "new"}]
        assert not_found is False


class TestVerify:
    def test_verify_empty_list(self):
        """Test verify with empty citations list."""
        kept, dropped = verify(Path("/tmp"), [])
        assert kept == []
        assert dropped == 0

    def test_verify_valid_quote_kept(self, tmp_path):
        """Test that valid quote is kept."""
        # Create a test file
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    return 'world'", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1",
            "quote": "def hello():"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert len(kept) == 1
        assert kept[0]["file"] == "test.py"
        assert dropped == 0

    def test_verify_valid_quote_multiline_whitespace_difference(self, tmp_path):
        """Test that multiline whitespace differences are normalized."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    return  'world'", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1-2",
            "quote": "def hello(): return 'world'"  # normalized: newline and extra spaces removed
        }]
        kept, dropped = verify(tmp_path, citations)
        assert len(kept) == 1
        assert dropped == 0

    def test_verify_wrong_quote_dropped(self, tmp_path):
        """Test that wrong quote is dropped."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello():\n    return 'world'", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1",
            "quote": "wrong quote content"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_missing_file_dropped(self, tmp_path):
        """Test that citations referencing missing files are dropped."""
        citations = [{
            "file": "nonexistent.py",
            "lines": "1",
            "quote": "some quote"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_path_escape_dropped(self, tmp_path):
        """Test that path escapes like ../../etc/passwd are dropped."""
        # Create a file in tmp
        test_file = tmp_path / "test.py"
        test_file.write_text("content", encoding="utf-8")

        citations = [{
            "file": "../../etc/passwd",
            "lines": "1",
            "quote": "root"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_symlink_outside_repo_dropped(self, tmp_path):
        """Test that symlinks pointing outside repo are dropped."""
        # Create a file outside the repo
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("outside content", encoding="utf-8")

        # Create a symlink inside repo pointing outside
        inside = tmp_path / "link_to_outside.txt"
        inside.symlink_to(outside)

        citations = [{
            "file": "link_to_outside.txt",
            "lines": "1",
            "quote": "outside content"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_missing_file_key_dropped(self, tmp_path):
        """Test that citations missing the 'file' key are dropped."""
        citations = [{
            "lines": "1",
            "quote": "some quote"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_missing_quote_key_dropped(self, tmp_path):
        """Test that citations missing the 'quote' key are dropped."""
        test_file = tmp_path / "test.py"
        test_file.write_text("content", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_empty_string_quote_dropped(self, tmp_path):
        """Test that empty string quotes are dropped (trust bypass prevention)."""
        test_file = tmp_path / "test.py"
        test_file.write_text("any content", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1",
            "quote": ""  # Empty string matches everything
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_whitespace_only_quote_dropped(self, tmp_path):
        """Test that whitespace-only quotes are dropped (normalize to empty)."""
        test_file = tmp_path / "test.py"
        test_file.write_text("any content", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1",
            "quote": "   \n\t  "  # Whitespace only
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_non_string_file_dropped(self, tmp_path):
        """Test that citations with non-string file values are dropped."""
        test_file = tmp_path / "test.py"
        test_file.write_text("content", encoding="utf-8")

        citations = [{
            "file": 5,  # Non-string file
            "lines": "1",
            "quote": "content"
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1

    def test_verify_non_string_quote_dropped(self, tmp_path):
        """Test that citations with non-string quote values are dropped."""
        test_file = tmp_path / "test.py"
        test_file.write_text("content", encoding="utf-8")

        citations = [{
            "file": "test.py",
            "lines": "1",
            "quote": None  # Non-string quote
        }]
        kept, dropped = verify(tmp_path, citations)
        assert kept == []
        assert dropped == 1
