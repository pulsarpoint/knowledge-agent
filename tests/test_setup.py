import base64
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.setup import initialize


class SetupTests(unittest.TestCase):
    def test_setup_preserves_credentials_and_existing_docs(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            initialize(root)
            first = (root / ".env").read_text(encoding="utf-8")
            values = dict(re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", first, re.MULTILINE))
            self.assertEqual(len(base64.urlsafe_b64decode(values["OAUTH2_PROXY_COOKIE_SECRET"])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values["CREDENTIAL_ENCRYPTION_KEY"])), 32)
            self.assertTrue((root / ".local/code").is_dir())
            self.assertEqual(values["RG_DATA"], f"'{root.resolve() / '.local/rag-data'}'")
            self.assertNotEqual(values["OAUTH2_PROXY_COOKIE_SECRET"], values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"])
            self.assertNotEqual(values["OAUTH2_PROXY_CLIENT_SECRET"], values["ADMIN_OAUTH2_PROXY_CLIENT_SECRET"])
            self.assertTrue(values["POSTGRES_PASSWORD"])
            self.assertTrue(values["APP_ADMIN_PASSWORD"])
            self.assertEqual((root / ".env").stat().st_mode & 0o777, 0o600)
            (root / ".local/docs/index.md").write_text("User documentation", encoding="utf-8")
            initialize(root)
            self.assertEqual((root / ".env").read_text(encoding="utf-8"), first)
            self.assertEqual((root / ".local/docs/index.md").read_text(encoding="utf-8"), "User documentation")

    def test_existing_database_only_configuration_is_extended(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("POSTGRES_PASSWORD=keep-this\nPOSTGRES_PORT=15438\n", encoding="utf-8")
            initialize(root)
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
            initialize(root)
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            config = json.loads(result.stdout)
            # Compose escapes dollars when serializing config for safe reloading.
            expected = str(root.resolve() / ".local/rag-data").replace("$", "$$")
            for name in ("rag", "rag-worker"):
                service = config["services"][name]
                mount = next(volume for volume in service["volumes"] if volume["target"] == "/work/rag-data")
                self.assertEqual(mount["source"], expected)
                self.assertEqual(service["environment"]["RG_DATA"], expected)

    def test_setup_preserves_an_explicit_host_mount(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("RG_DATA='/srv/knowledge sources'\n", encoding="utf-8")
            initialize(root)
            self.assertIn("RG_DATA='/srv/knowledge sources'\n", (root / ".env").read_text(encoding="utf-8"))
