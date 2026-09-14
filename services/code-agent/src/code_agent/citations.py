"""Citation parsing and verification: extract citations from runtime output
and verify them against real repo files."""
from __future__ import annotations

import json
import re
from pathlib import Path

CITATIONS_MARKER = "CITATIONS:"


def parse_answer(text: str) -> tuple[str, list[dict], bool]:
    """Parse the runtime's final message into answer, citations, and not_found flag.

    Args:
        text: The raw output from the runtime, potentially containing answer text,
              NOT_FOUND marker, and/or CITATIONS: marker with JSON array.

    Returns:
        A tuple of:
        - answer: The answer text with marker and array removed (everything
                  before the LAST marker).
        - citations: List of citation dicts parsed from JSON, or [] if missing/malformed.
        - not_found: True if the message (stripped) starts with "NOT_FOUND", else False.

    The JSON array is expected on the lines AFTER the LAST "CITATIONS:" marker.
    Marker and array are removed from the answer text (everything before the LAST marker
    becomes the answer). Each citation entry should have: {"file": str, "lines": str, "quote": str}.
    """
    stripped = text.strip()

    # Check for NOT_FOUND marker
    not_found = stripped.startswith("NOT_FOUND")

    # Find the LAST occurrence of CITATIONS: marker
    # A source quote can itself contain "CITATIONS:". Only a literal marker
    # line is a delimiter; JSON-escaped text inside a quote is evidence.
    markers = list(re.finditer(r'(?m)^CITATIONS:[ \t]*\r?$', text))
    last_marker_pos = markers[-1].start() if markers else -1

    if last_marker_pos == -1:
        # No marker found
        return text, [], not_found

    # Extract the answer: everything before the LAST marker
    answer = text[:last_marker_pos].rstrip()

    # Extract the part after the LAST marker (potential JSON)
    after_last_marker = text[last_marker_pos + len(CITATIONS_MARKER):]

    # Try to parse JSON from the remaining text
    citations = []
    try:
        # Strip whitespace and parse JSON
        json_text = after_last_marker.strip()
        parsed = json.loads(json_text)
        if isinstance(parsed, list):
            citations = parsed
    except (json.JSONDecodeError, ValueError):
        # Malformed JSON, keep empty citations list
        pass

    return answer, citations, not_found


def verify(repo_root: Path, citations: list[dict]) -> tuple[list[dict], int]:
    """Verify citations against real files in the repository.

    Args:
        repo_root: The root directory of the repository to verify against.
        citations: List of citation dicts with "file", "lines", and "quote" keys.

    Returns:
        A tuple of:
        - kept: List of citations that passed verification (valid file and matching quote).
        - dropped_count: Number of citations dropped due to invalid file or quote mismatch.

    A citation is kept if:
    - It has both "file" and "quote" keys, and both are strings (non-strings dropped).
    - The resolved file path is strictly under repo_root (symlink escapes are dropped).
    - The quote (whitespace-normalized) is non-empty and occurs in the file text (also normalized).
    """
    repo_root = Path(repo_root).resolve()
    kept = []
    dropped_count = 0

    for citation in citations:
        # Check for required keys and types
        if not isinstance(citation.get("file"), str) or not isinstance(citation.get("quote"), str):
            dropped_count += 1
            continue

        file_path_str = citation["file"]
        quote = citation["quote"]

        try:
            # Resolve the file path relative to repo_root
            file_path = (repo_root / file_path_str).resolve()

            # Check if resolved path is strictly under repo_root
            # This includes symlink escape detection
            try:
                file_path.relative_to(repo_root)
            except ValueError:
                # Path is outside repo_root
                dropped_count += 1
                continue

            # Check if file exists and is a file (not a directory)
            if not file_path.is_file():
                dropped_count += 1
                continue

            # Read file and normalize whitespace
            file_text = file_path.read_text(encoding="utf-8")
            normalized_file = " ".join(file_text.split())
            normalized_quote = " ".join(quote.split())

            # Reject empty quotes (empty string matches everything, a trust bypass)
            if not normalized_quote:
                dropped_count += 1
                continue

            # Check if normalized quote occurs in normalized file text
            if normalized_quote in normalized_file:
                kept.append(citation)
            else:
                dropped_count += 1
        except (OSError, UnicodeDecodeError):
            # File read error
            dropped_count += 1
            continue

    return kept, dropped_count
