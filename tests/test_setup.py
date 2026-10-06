import base64
import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.setup import initialize


SUBFOLDERS = ("code", "code-index", "docs", "generated-documents", "jira")
OLD_OVERRIDES = ("CODE_PATH", "CODE_INDEX_PATH", "RG_DATA", "GENERATED_DOCS_PATH", "DOCS_PATH", "RAG_SOURCE_ROOT")


def run_setup(root: Path, data_root: str | None = None) -> str:
    """initialize() with its printed output captured."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        initialize(root, data_root)
    return output.getvalue()



def project(directory: str) -> Path:
    source = Path(__file__).resolve().parents[1]
    root = Path(directory) / "checkout"
    root.mkdir()
    shutil.copy(source / ".env.example", root / ".env.example")
    shutil.copytree(source / "examples", root / "examples")
    return root


def env_values(root: Path) -> dict:
    return dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", (root / ".env").read_text(encoding="utf-8"), re.MULTILINE))


class SetupTests(unittest.TestCase):
    def test_setup_requires_an_absolute_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            with self.assertRaises(SystemExit) as missing:
                initialize(root)
            self.assertIn("make setup DATA_ROOT=", str(missing.exception))
            self.assertFalse((root / ".env").exists())
            with self.assertRaises(SystemExit) as relative:
                initialize(root, "data/knowledge")
            self.assertIn("absolute", str(relative.exception))
            self.assertFalse((root / ".local").exists())

    def test_setup_creates_data_folders_under_the_root_and_records_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "opt" / "knowledge agent"
            output = run_setup(root, str(data))
            values = env_values(root)
            self.assertEqual(values["DATA_ROOT"], f"'{data}'")
            self.assertEqual({key: values[key] for key in ("CODE_FOLDER", "CODE_INDEX_FOLDER", "DOCS_FOLDER",
                                                           "GENERATED_DOCS_FOLDER", "JIRA_FOLDER")},
                             {"CODE_FOLDER": "code", "CODE_INDEX_FOLDER": "code-index", "DOCS_FOLDER": "docs",
                              "GENERATED_DOCS_FOLDER": "generated-documents", "JIRA_FOLDER": "jira"})
            self.assertEqual(sorted(entry.name for entry in data.iterdir()), sorted(SUBFOLDERS))
            self.assertTrue(any((data / "docs").iterdir()))
            self.assertFalse((root / ".local").exists())
            for key in OLD_OVERRIDES:
                self.assertNotIn(key, values)
            self.assertNotIn("Warning", output)
            # Later runs reuse the recorded root without an argument.
            initialize(root)
            self.assertEqual(env_values(root)["DATA_ROOT"], f"'{data}'")

    def test_setup_uses_custom_relative_folders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (root / ".env").write_text("CODE_FOLDER=sources/code\nJIRA_FOLDER='exports/jira'\n", encoding="utf-8")
            run_setup(root, str(data))
            self.assertTrue((data / "sources" / "code").is_dir())
            self.assertTrue((data / "exports" / "jira").is_dir())
            self.assertFalse((data / "code").exists() or (data / "jira").exists())
            values = env_values(root)
            self.assertEqual((values["CODE_FOLDER"], values["JIRA_FOLDER"]), ("sources/code", "'exports/jira'"))
            self.assertEqual(values["DOCS_FOLDER"], "docs")

    def test_setup_refuses_invalid_and_overlapping_folders(self) -> None:
        cases = {
            "JIRA_FOLDER=/srv/jira": "JIRA_FOLDER must be a folder inside DATA_ROOT such as jira",
            "CODE_FOLDER=../code": "CODE_FOLDER must be a folder inside DATA_ROOT such as code",
            "CODE_FOLDER=a/../b": "CODE_FOLDER must be a folder",
            "CODE_FOLDER=code/": "CODE_FOLDER must be a folder",
            "CODE_FOLDER=.hidden": "CODE_FOLDER must be a folder",
            "DOCS_FOLDER=.docs": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=docs/.git": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=a//b": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=my docs": "DOCS_FOLDER must be a folder",
            "CODE_INDEX_FOLDER=code/index": "CODE_FOLDER (code) and CODE_INDEX_FOLDER (code/index) overlap",
            "DOCS_FOLDER=jira": "DOCS_FOLDER (jira) and JIRA_FOLDER (jira) overlap",
        }
        for line, message in cases.items():
            with self.subTest(line), tempfile.TemporaryDirectory() as directory:
                root = project(directory)
                (root / ".env").write_text(line + "\n", encoding="utf-8")
                data = Path(directory) / "data"
                with self.assertRaises(SystemExit) as error:
                    initialize(root, str(data))
                self.assertIn(message, str(error.exception))
                self.assertEqual((root / ".env").read_text(encoding="utf-8"), line + "\n")
                self.assertFalse(data.exists())

    def test_setup_refuses_old_overrides_naming_the_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            cases = {
                f"CODE_PATH='{data}/sources/code'": "CODE_PATH is no longer used. Replace it in .env with CODE_FOLDER=sources/code.",
                "DOCS_PATH=/srv/docs": f"DOCS_PATH is no longer used. Move /srv/docs into {data}, then replace DOCS_PATH in .env with DOCS_FOLDER=<folder inside DATA_ROOT>.",
                "RG_DATA=/srv/rag": f"RG_DATA is no longer used; remove it from .env. Jira exports now go to JIRA_FOLDER inside DATA_ROOT (default jira); make setup moves {data}/rag-data/jira there.",
                "RAG_SOURCE_ROOT=/work/rag-data": "RAG_SOURCE_ROOT is no longer used; remove it from .env.",
            }
            for line, message in cases.items():
                with self.subTest(line):
                    root = Path(tempfile.mkdtemp(dir=directory))
                    shutil.copy(Path(__file__).resolve().parents[1] / ".env.example", root / ".env.example")
                    shutil.copytree(Path(__file__).resolve().parents[1] / "examples", root / "examples")
                    (root / ".env").write_text(line + "\n", encoding="utf-8")
                    with self.assertRaises(SystemExit) as error:
                        initialize(root, str(data))
                    self.assertEqual(str(error.exception), message)
                    self.assertEqual((root / ".env").read_text(encoding="utf-8"), line + "\n")
                    self.assertFalse(data.exists())

    def test_setup_moves_rag_data_jira_once_and_never_over_an_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            (data / "rag-data" / "jira" / "DEMO-1.md").write_text("# DEMO-1", encoding="utf-8")
            (data / "rag-data" / "demo").mkdir()
            output = run_setup(root, str(data))
            self.assertIn(f"Moved {data / 'rag-data' / 'jira'} to {data / 'jira'}.", output)
            self.assertEqual((data / "jira" / "DEMO-1.md").read_text(encoding="utf-8"), "# DEMO-1")
            self.assertFalse((data / "rag-data" / "jira").exists())
            self.assertTrue((data / "rag-data" / "demo").is_dir())  # other rag-data folders are never touched
            self.assertIn("not configured folders: rag-data.", output)
            again = run_setup(root)
            self.assertNotIn("Moved", again)
            self.assertEqual((data / "jira" / "DEMO-1.md").read_text(encoding="utf-8"), "# DEMO-1")
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            (data / "rag-data" / "jira" / "OLD-1.md").write_text("old", encoding="utf-8")
            (data / "jira").mkdir()
            (data / "jira" / "NEW-1.md").write_text("new", encoding="utf-8")
            output = run_setup(root, str(data))
            self.assertNotIn("Moved", output)
            self.assertEqual((data / "rag-data" / "jira" / "OLD-1.md").read_text(encoding="utf-8"), "old")
            self.assertEqual(sorted(p.name for p in (data / "jira").iterdir()), ["NEW-1.md"])
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "rag-data" / "jira").mkdir(parents=True)
            output = run_setup(root, str(data))
            self.assertIn(f"Removed the empty folder {data / 'rag-data'}.", output)
            self.assertFalse((data / "rag-data").exists())
            self.assertNotIn("Warning", output)

    def test_setup_warns_about_other_top_level_entries_and_moves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "backups").mkdir(parents=True)
            (data / "users").mkdir()
            (data / "startup.log").write_text("log", encoding="utf-8")
            output = run_setup(root, str(data))
            self.assertIn(f"Warning: {data} holds entries that are not configured folders: backups, startup.log, users. "
                          "RAG can see every folder in DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE "
                          "folder. Nothing was moved.", output)
            self.assertTrue((data / "backups").is_dir() and (data / "users").is_dir())
            self.assertEqual((data / "startup.log").read_text(encoding="utf-8"), "log")

    def test_setup_fills_empty_folders_accepts_siblings_and_ignores_ds_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (root / ".env").write_text("CODE_FOLDER=\nCODE_INDEX_FOLDER=code2\n", encoding="utf-8")
            data.mkdir()
            (data / ".DS_Store").write_text("x", encoding="utf-8")
            output = run_setup(root, str(data))
            values = env_values(root)
            self.assertEqual((values["CODE_FOLDER"], values["CODE_INDEX_FOLDER"]), ("code", "code2"))
            self.assertTrue((data / "code").is_dir() and (data / "code2").is_dir())
            self.assertNotIn("Warning", output)

    def test_setup_refuses_old_override_inside_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (root / ".env").write_text(f"DOCS_PATH={data}/docs\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as error:
                initialize(root, str(data))
            self.assertIn("DOCS_FOLDER=docs", str(error.exception))

    def test_setup_reports_an_unwritable_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            locked = Path(directory) / "locked"
            locked.mkdir(mode=0o500)
            try:
                with self.assertRaises(SystemExit) as error:
                    initialize(root, str(locked / "knowledge-agent"))
                self.assertIn("sudo mkdir", str(error.exception))
            finally:
                locked.chmod(0o700)

    def test_setup_preserves_credentials_and_existing_docs(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            data = root / "data"
            initialize(root, str(data))
            first = (root / ".env").read_text(encoding="utf-8")
            values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", first, re.MULTILINE))
            self.assertEqual(len(base64.urlsafe_b64decode(values["OAUTH2_PROXY_COOKIE_SECRET"])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values["CREDENTIAL_ENCRYPTION_KEY"])), 32)
            self.assertTrue((data / "code").is_dir())
            self.assertNotEqual(values["OAUTH2_PROXY_COOKIE_SECRET"], values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"])
            self.assertNotEqual(values["OAUTH2_PROXY_CLIENT_SECRET"], values["ADMIN_OAUTH2_PROXY_CLIENT_SECRET"])
            self.assertTrue(values["POSTGRES_PASSWORD"])
            self.assertTrue(values["APP_ADMIN_PASSWORD"])
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            (data / "docs/index.md").write_text("User documentation", encoding="utf-8")
            initialize(root)
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), first)
            self.assertEqual((data / "docs/index.md").read_text(encoding="utf-8"), "User documentation")

    def test_existing_database_only_configuration_is_extended(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("POSTGRES_PASSWORD=keep-this\nPOSTGRES_PORT=15438\n", encoding="utf-8")
            initialize(root, str(root / "data"))
            text = (root / ".env").read_text(encoding="utf-8")
            self.assertIn("POSTGRES_PASSWORD=keep-this\n", text)
            self.assertIn("POSTGRES_PORT=15438\n", text)
            self.assertIn("WEB_PORT=8180\n", text)
            self.assertRegex(text, r"APP_ADMIN_PASSWORD=\S+")

    def test_compose_mounts_the_data_root_read_only_and_writer_folders_writable(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with $dollars #and 'quotes'"
            root.mkdir()
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("CODE_FOLDER=src/code\nJIRA_FOLDER=exports/jira\n", encoding="utf-8")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            services = json.loads(result.stdout)["services"]
            # Compose escapes dollars when serializing config for safe reloading.
            data = str(root / "data").replace("$", "$$")
            folders = {"code": "src/code", "index": "code-index", "documents": "generated-documents", "jira": "exports/jira"}
            writers = {"rag": ["jira"], "rag-worker": [], "docs-wiki": [], "shell-worker": [],
                       "admin-api": ["code", "index", "documents"], "repository-worker": ["code", "index"],
                       "documentation-worker": ["index", "documents"]}
            for name, writable in writers.items():
                mounts = sorted((v["source"], v["target"], bool(v.get("read_only"))) for v in services[name]["volumes"]
                                if v["target"].startswith("/work/data"))
                expected = sorted([(data, "/work/data", True)] + [
                    (f"{data}/{folders[key]}", f"/work/data/{folders[key]}", False) for key in writable])
                self.assertEqual(mounts, expected, name)
            for name in ("rag", "admin-api"):
                env = services[name]["environment"]
                self.assertEqual(env["DATA_ROOT_DIR"], "/work/data")
                self.assertEqual(env["DATA_ROOT"], data)
                self.assertEqual(env["CODE_ROOT"], "/work/data/src/code")
                self.assertEqual(env["JIRA_EXPORT_ROOT"], "/work/data/exports/jira")
                self.assertEqual(env["DOCUMENT_OUTPUT_ROOT"], "/work/data/generated-documents")
            for service in services.values():
                for volume in service.get("volumes", []):
                    self.assertNotIn(".local", volume.get("source", ""))

    def test_setup_generates_reader_agent_key(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            initialize(root, str(root / "data"))
            values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", (root / ".env").read_text(encoding="utf-8"), re.MULTILINE))
            self.assertGreaterEqual(len(values["READER_AGENT_KEY"]), 32)
            self.assertNotEqual(values["READER_AGENT_KEY"], values["RAG_BACKOFFICE_KEY"])
