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


SUBFOLDERS = ("code", "code-index", "docs")
OLD_OVERRIDES = ("CODE_PATH", "CODE_INDEX_PATH", "DOCS_PATH")


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
            self.assertEqual({key: values[key] for key in ("CODE_FOLDER", "CODE_INDEX_FOLDER", "DOCS_FOLDER")},
                             {"CODE_FOLDER": "code", "CODE_INDEX_FOLDER": "code-index", "DOCS_FOLDER": "docs"})
            for key in ("GENERATED_DOCS_FOLDER", "JIRA_FOLDER", "RAG_BACKOFFICE_KEY"):
                self.assertNotIn(key, values)
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
            (root / ".env").write_text("CODE_FOLDER=sources/code\nCODE_INDEX_FOLDER='indexes/code'\n", encoding="utf-8")
            run_setup(root, str(data))
            self.assertTrue((data / "sources" / "code").is_dir())
            self.assertTrue((data / "indexes" / "code").is_dir())
            self.assertFalse((data / "code").exists() or (data / "code-index").exists())
            values = env_values(root)
            self.assertEqual((values["CODE_FOLDER"], values["CODE_INDEX_FOLDER"]), ("sources/code", "'indexes/code'"))
            self.assertEqual(values["DOCS_FOLDER"], "docs")

    def test_setup_refuses_invalid_and_overlapping_folders(self) -> None:
        cases = {
            "CODE_INDEX_FOLDER=/srv/index": "CODE_INDEX_FOLDER must be a folder inside DATA_ROOT such as code-index",
            "CODE_FOLDER=../code": "CODE_FOLDER must be a folder inside DATA_ROOT such as code",
            "CODE_FOLDER=a/../b": "CODE_FOLDER must be a folder",
            "CODE_FOLDER=code/": "CODE_FOLDER must be a folder",
            "CODE_FOLDER=.hidden": "CODE_FOLDER must be a folder",
            "DOCS_FOLDER=.docs": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=docs/.git": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=a//b": "DOCS_FOLDER must be a folder",
            "DOCS_FOLDER=my docs": "DOCS_FOLDER must be a folder",
            "CODE_INDEX_FOLDER=code/index": "CODE_FOLDER (code) and CODE_INDEX_FOLDER (code/index) overlap",
            "DOCS_FOLDER=code": "CODE_FOLDER (code) and DOCS_FOLDER (code) overlap",
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

    def test_setup_warns_about_other_top_level_entries_and_moves_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = project(directory)
            data = Path(directory) / "data"
            (data / "backups").mkdir(parents=True)
            (data / "users").mkdir()
            (data / "startup.log").write_text("log", encoding="utf-8")
            output = run_setup(root, str(data))
            self.assertIn(f"Warning: {data} holds entries that are not configured folders: backups, startup.log, users. "
                          "Every service sees all of DATA_ROOT; move them out, for example to a sibling DATA_ARCHIVE "
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
            self.assertEqual(len(base64.urlsafe_b64decode(values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"])), 32)
            self.assertEqual(len(base64.urlsafe_b64decode(values["CREDENTIAL_ENCRYPTION_KEY"])), 32)
            self.assertTrue((data / "code").is_dir())
            self.assertNotEqual(values["ADMIN_OAUTH2_PROXY_COOKIE_SECRET"], values["ADMIN_OAUTH2_PROXY_CLIENT_SECRET"])
            self.assertNotIn("READER_AGENT_KEY", values)
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
            self.assertIn("ADMIN_PORT=8183\n", text)
            self.assertRegex(text, r"APP_ADMIN_PASSWORD=\S+")

    def test_compose_mounts_the_data_root_read_only_and_writer_folders_writable(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project with $dollars #and 'quotes'"
            root.mkdir()
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            (root / ".env").write_text("CODE_FOLDER=src/code\n", encoding="utf-8")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            services = json.loads(result.stdout)["services"]
            # Compose escapes dollars when serializing config for safe reloading.
            data = str(root / "data").replace("$", "$$")
            folders = {"code": "src/code", "index": "code-index"}
            writers = {"shell-worker": [], "admin-api": ["code", "index"], "repository-worker": ["code", "index"],
                       "agent-session-worker": []}
            for name, writable in writers.items():
                mounts = sorted((v["source"], v["target"], bool(v.get("read_only"))) for v in services[name]["volumes"]
                                if v["target"].startswith("/work/data"))
                expected = sorted([(data, "/work/data", True)] + [
                    (f"{data}/{folders[key]}", f"/work/data/{folders[key]}", False) for key in writable])
                self.assertEqual(mounts, expected, name)
            for retired in ("docs-wiki", "web", "oauth2-proxy", "documentation-worker", "rag", "rag-worker"):
                self.assertNotIn(retired, services)
            env = services["admin-api"]["environment"]
            self.assertEqual(env["DATA_ROOT_DIR"], "/work/data")
            self.assertEqual(env["DATA_ROOT"], data)
            self.assertEqual(env["CODE_ROOT"], "/work/data/src/code")
            for gone in ("JIRA_EXPORT_ROOT", "DOCUMENT_OUTPUT_ROOT"):
                self.assertNotIn(gone, env)
            for service in services.values():
                for volume in service.get("volumes", []):
                    self.assertNotIn(".local", volume.get("source", ""))

    def test_agent_session_worker_mounts_networks_and_environment(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"), "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            config = json.loads(result.stdout)
            worker = config["services"]["agent-session-worker"]
            data = str(root / "data").replace("$", "$$")
            self.assertEqual(sorted((v["type"], v["source"], v["target"], bool(v.get("read_only"))) for v in worker["volumes"]),
                             [("bind", data, "/work/data", True), ("volume", "agent-workspaces", "/work/agent-workspaces", False)])
            self.assertEqual(worker["entrypoint"], ["/services/admin-api/.venv/bin/knowledge-agent-session-worker"])
            self.assertNotIn("ports", worker)
            # Like admin-api: PostgreSQL, MCP servers and model providers on default; shell-worker on agent-shell.
            self.assertEqual(set(worker["networks"]), {"default", "agent-shell"})
            env = worker["environment"]
            self.assertEqual((env["AGENT_SHELL_URL"], env["AGENT_WORKSPACE_ROOT"], env["CODE_ROOT"], env["DATA_ROOT_DIR"],
                              env["DOCS_WIKI_PHOENIX_ENDPOINT"], env["AGENT_SESSION_MAX_SESSIONS"],
                              env["AGENT_MAX_SUBAGENTS"]),
                             ("http://shell-worker:8796", "/work/agent-workspaces", "/work/data/code", "/work/data",
                              "http://phoenix:6006/v1/traces", "4", "4"))
            for name in ("PGHOST", "PGPASSWORD", "CREDENTIAL_ENCRYPTION_KEY", "AUTH_ISSUER_URL",
                         "DOCS_WIKI_PHOENIX_API_KEY"):
                self.assertIn(name, env)
            self.assertEqual(set(worker["depends_on"]), {"migrate", "shell-worker"})

    def test_no_service_is_rag_or_depends_on_it(self) -> None:
        source = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copy(source / ".env.example", root / ".env.example")
            shutil.copytree(source / "examples", root / "examples")
            run_setup(root, str(root / "data"))
            result = subprocess.run(
                ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(source / "deploy/compose.yaml"),
                 "--profile", "jira-mock", "config", "--format", "json"],
                capture_output=True, text=True, check=True,
            )
            services = json.loads(result.stdout)["services"]
            self.assertFalse({"rag", "rag-worker"} & set(services))
            for name, service in services.items():
                self.assertNotIn("rag", service.get("depends_on", {}), name)
                self.assertNotIn("rag-worker", service.get("depends_on", {}), name)
                for key, value in (service.get("environment") or {}).items():
                    self.assertNotIn("RAG", key, name)
                    self.assertNotIn("rag:", str(value), name)
            self.assertNotIn("RAG_BACKOFFICE_KEY", env_values(root))

