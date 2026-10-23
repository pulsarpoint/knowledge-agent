"""RAG is gone: no rag/RAG and no collection (the RAG concept) is left in the code, the stack or the current docs."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = (
    "services/admin-api/src",
    "services/admin-api/README.md",
    "services/admin-ui/app",
    "services/admin-ui/README.md",
    "packages/llm-settings/src",
    "deploy",
    "Makefile",
    "scripts",
    "tests",
    "README.md",
    "docs",
    "examples",
)
SKIPPED_DIRS = {"node_modules", "__pycache__", ".react-router", "superpowers", "migrations"}
# Files allowed to name RAG, each with its reason.
EXCEPTIONS = {
    "tests/test_no_rag.py": "this test: the patterns themselves",
    "tests/test_setup.py": "asserts the stack has no rag service, dependency or key (Review Focus 5)",
    "services/admin-ui/app/components/__tests__/adminLayout.test.tsx": "asserts the RAG navigation is gone",
    "docs/migrations.md": "migration history, including 0132-0134 that removed RAG",
}
# rag, RAG and Rag as a word or an identifier part (rag_files, ragPages, RagSearch, resolve_rag, resolveRag), never
# inside a word such as storage, average, drag or fragment.
RAG = re.compile(r"(?:(?<![A-Za-z])(?:rag|RAG|Rag)|(?<=[a-z])(?:Rag|RAG))(?![a-z])")
# collection, collections, collection_ids, collectionId, all_collections, agent_collections: the RAG concept.
COLLECTION = re.compile(r"(?i)(?<![a-z])collection(?:s|_ids?|ids?)?(?![a-z])")
PYTHON_MODULE = re.compile(r"\b(?:from|import) collections\b")  # Python's collections module is not the concept
# In the docs, a paragraph that starts with "Migration `0..." records what a migration did: history, kept as written.
HISTORY = re.compile(r"^(?:- )?Migrations? `\d{4}")


def scanned_files():
    for name in SCANNED:
        path = ROOT / name
        files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for file in files:
            relative = file.relative_to(ROOT)
            if SKIPPED_DIRS & set(relative.parts) or relative.as_posix() in EXCEPTIONS:
                continue
            try:
                yield relative.as_posix(), file.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # images and other binary files


def current_lines(path: str, text: str):
    """Numbered lines, without the history paragraphs of a Markdown file."""
    history = False
    for number, line in enumerate(text.splitlines(), 1):
        if path.endswith(".md"):
            if not line.strip():
                history = False
                continue
            if HISTORY.match(line):
                history = True
        if not history:
            yield number, line


def leftovers(text: str, path: str = "") -> list[str]:
    found = []
    for number, line in current_lines(path, text):
        if RAG.search(line) or (COLLECTION.search(line) and not PYTHON_MODULE.search(line)):
            found.append(f"{number}: {line.strip()[:120]}")
    return found


class NoRagTests(unittest.TestCase):
    def test_the_patterns_match_rag_and_collections_but_not_ordinary_words(self) -> None:
        for line in ("RAG search", "rag_files", "ragPages", "RagSearch", "resolve_rag", "resolveRag", "the rag service",
                     "a collection", "collection_ids", "all_collections", "collectionId", "Collections"):
            self.assertTrue(leftovers(line), line)
        for line in ("storage", "Storage", "average", "drag", "fragment", "leverage", "STORAGE",
                     "from collections.abc import Mapping", "import collections", "recollect"):
            self.assertFalse(leftovers(line), line)

    def test_history_paragraphs_of_the_docs_are_skipped(self) -> None:
        text = "Migration `0067_data_root` moved RAG paths\nand collections.\n\nRAG is current."
        self.assertEqual(leftovers(text, "docs/x.md"), ["4: RAG is current."])
        self.assertEqual(len(leftovers(text, "x.py")), 3)

    def test_no_rag_and_no_collection_is_left(self) -> None:
        found = {path: lines for path, text in scanned_files() if (lines := leftovers(text, path))}
        self.assertEqual(found, {})

    def test_the_rag_service_and_its_files_are_gone(self) -> None:
        for path in ("services/rag", "deploy/Dockerfile.rag", "docs/rag.md", "services/admin-api/src/knowledge_admin/rag",
                     "services/admin-ui/app/features/rag"):
            self.assertFalse((ROOT / path).exists(), path)


if __name__ == "__main__":
    unittest.main()
