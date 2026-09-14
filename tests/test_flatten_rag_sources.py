import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from scripts.flatten_rag_sources import flatten


class FlattenRagSourcesTests(unittest.TestCase):
    def test_move_preserves_nested_content_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project = Path(directory), uuid4()
            legacy = root / str(project)
            (legacy / "docs/payments").mkdir(parents=True)
            (legacy / "docs/payments/readme.md").write_bytes(b"Existing source content")
            self.assertEqual(flatten(root, project), ["docs"])
            self.assertEqual((root / "docs/payments/readme.md").read_bytes(), b"Existing source content")
            self.assertFalse(legacy.exists())
            self.assertEqual(flatten(root, project), [])

    def test_conflict_leaves_every_source_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project = Path(directory), uuid4()
            legacy = root / str(project)
            (legacy / "a").mkdir(parents=True)
            (legacy / "z").mkdir()
            (root / "z").mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                flatten(root, project)
            self.assertTrue((legacy / "a").is_dir())
            self.assertTrue((legacy / "z").is_dir())
            self.assertFalse((root / "a").exists())

    def test_legacy_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root, project = Path(directory), uuid4()
            (root / "outside").mkdir()
            (root / str(project)).symlink_to(root / "outside", target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                flatten(root, project)
