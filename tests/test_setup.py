import base64
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.setup import initialize


SUBFOLDERS = ("code", "code-index", "rag-data", "generated-documents", "docs")


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
            initialize(root, str(data))
            self.assertEqual(env_values(root)["DATA_ROOT"], f"'{data}'")
            for name in SUBFOLDERS:
                self.assertTrue((data / name).is_dir(), name)
            self.assertTrue(any((data / "docs").iterdir()))
            self.assertFalse((root / ".local").exists())
            for key in ("CODE_PATH", "CODE_INDEX_PATH", "RG_DATA", "GENERATED_DOCS_PATH", "DOCS_PATH"):
                self.assertNotIn(key, env_values(root))
            # Later runs reuse the recorded root without an argument.
            initialize(root)
            self.assertEqual(env_values(root)["DATA_ROOT"], f"'{data}'")

    def test_setup_rejects_relative_folder_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            (root / ".env").write_text("CODE_PATH=../.local/code\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as error:
                initialize(root, str(Path(directory) / "data"))
            self.assertIn("CODE_PATH", str(error.exception))

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

    def test_compose_mount_and_display_use_the_same_literal_host_path(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with $dollars #and 'quotes'"
            root.mkdir()
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            initialize(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            config = json.loads(result.stdout)
            # Compose escapes dollars when serializing config for safe reloading.
            data = str(root / "data").replace("$", "$$")
            for name in ("rag", "rag-worker"):
                service = config["services"][name]
                mount = next(volume for volume in service["volumes"] if volume["target"] == "/work/rag-data")
                self.assertEqual(mount["source"], data + "/rag-data")
                self.assertEqual(service["environment"]["RG_DATA"], data + "/rag-data")
            admin = config["services"]["admin-api"]
            sources = {volume["target"]: volume["source"] for volume in admin["volumes"]}
            self.assertEqual(sources["/work/code"], data + "/code")
            self.assertEqual(sources["/work/code-index"], data + "/code-index")
            self.assertEqual(sources["/work/generated-documents"], data + "/generated-documents")
            self.assertEqual(admin["environment"]["CODE_PATH"], data + "/code")
            for service in config["services"].values():
                for volume in service.get("volumes", []):
                    self.assertNotIn(".local", volume.get("source", ""))

    def test_setup_preserves_an_explicit_host_mount(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            sources = Path(directory) / "srv" / "knowledge sources"
            (root / ".env").write_text(f"RG_DATA='{sources}'\n", encoding="utf-8")
            initialize(root, str(root / "data"))
            self.assertIn(f"RG_DATA='{sources}'\n", (root / ".env").read_text(encoding="utf-8"))
            self.assertTrue(sources.is_dir())
            self.assertFalse((root / "data" / "rag-data").exists())

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
