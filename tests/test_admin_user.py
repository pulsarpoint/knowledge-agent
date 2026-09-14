import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.admin_user import ensure_admin


class AdminUserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = {
            "name": "knowledge-agent-v3",
            "services": {"keycloak": {"environment": {
                "KC_HOSTNAME": "http://localhost:8181",
                "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": "bootstrap-test-secret",
            }}},
        }
        self.group = {"id": "admin-group", "path": "/docs-admins"}
        self.user = {"id": "test-user", "username": "test-admin", "enabled": True}

    def test_new_account_saves_the_issued_password_privately(self) -> None:
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [self.group], [], None, [self.user], None, [self.group],
        ]) as request, contextlib.redirect_stdout(io.StringIO()) as output:
            path = ensure_admin(self.root, self.config, "test-admin", "test-admin@example.test")
        self.assertIsNotNone(path)
        credential = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(credential["username"], "test-admin")
        creation = next(call for call in request.call_args_list if call.args[0].endswith("/users"))
        self.assertEqual(creation.kwargs["payload"]["credentials"][0]["value"], credential["password"])
        self.assertFalse(creation.kwargs["payload"]["credentials"][0]["temporary"])
        self.assertTrue(creation.kwargs["payload"]["enabled"])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(path.parent.glob("*.pending-*")), [])
        self.assertNotIn(credential["password"], output.getvalue())
        self.assertNotIn("bootstrap-test-secret", output.getvalue())
        self.assertNotIn("test-token", output.getvalue())

    def test_repeating_does_not_reset_password_or_rewrite_credential_file(self) -> None:
        path = self.root / ".local/users/test-admin.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"username":"test-admin","password":"keep-me"}', encoding="utf-8")
        before = path.read_bytes()
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [self.group], [self.user], None, [self.group],
        ]) as request, contextlib.redirect_stdout(io.StringIO()):
            result = ensure_admin(self.root, self.config, "test-admin", "ignored@example.test")
        self.assertEqual(result, path)
        self.assertEqual(path.read_bytes(), before)
        mutations = [call for call in request.call_args_list if call.kwargs.get("method") == "PUT"]
        self.assertEqual(len(mutations), 1)
        self.assertTrue(mutations[0].args[0].endswith("/users/test-user/groups/admin-group"))

    def test_password_reset_requires_explicit_option(self) -> None:
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [self.group], [self.user], None, [self.user], None, [self.group],
        ]) as request, contextlib.redirect_stdout(io.StringIO()):
            path = ensure_admin(self.root, self.config, "test-admin", "test-admin@example.test", reset_password=True)
        credential = json.loads(path.read_text(encoding="utf-8"))
        reset = next(call for call in request.call_args_list if call.args[0].endswith("/reset-password"))
        self.assertEqual(reset.kwargs["payload"]["value"], credential["password"])
        self.assertFalse(reset.kwargs["payload"]["temporary"])

    def test_rejects_other_projects_remote_hosts_and_unsafe_usernames_before_auth(self) -> None:
        for project, origin, username in [
            ("knowledge-agent-v2", "http://localhost:8181", "test-admin"),
            ("knowledge-agent-v3", "https://example.test", "test-admin"),
            ("knowledge-agent-v3", "http://localhost:8181", "../escape"),
            ("knowledge-agent-v3", "http://localhost:8181/other", "test-admin"),
        ]:
            with self.subTest(project=project, origin=origin, username=username):
                self.config["name"] = project
                self.config["services"]["keycloak"]["environment"]["KC_HOSTNAME"] = origin
                with patch("scripts.admin_user.request_json") as request, self.assertRaises(ValueError):
                    ensure_admin(self.root, self.config, username, "test-admin@example.test")
                request.assert_not_called()

    def test_missing_root_admin_group_does_not_create_account(self) -> None:
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [{"id": "nested", "path": "/other/docs-admins"}],
        ]) as request, self.assertRaisesRegex(RuntimeError, "group is missing"):
            ensure_admin(self.root, self.config, "test-admin", "test-admin@example.test")
        self.assertFalse(any(call.args[0].endswith("/users") for call in request.call_args_list))
        self.assertFalse((self.root / ".local").exists())

    def test_disabled_account_is_preserved(self) -> None:
        self.user["enabled"] = False
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [self.group], [self.user],
        ]) as request, self.assertRaisesRegex(RuntimeError, "disabled"):
            ensure_admin(self.root, self.config, "test-admin", "test-admin@example.test")
        self.assertFalse(any(call.kwargs.get("method") == "PUT" for call in request.call_args_list))

    def test_failed_password_write_keeps_private_recovery_copy(self) -> None:
        with patch("scripts.admin_user.request_json", side_effect=[
            {"access_token": "test-token"}, [self.group], [], RuntimeError("connection lost"),
        ]), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(RuntimeError, "connection lost"):
            ensure_admin(self.root, self.config, "test-admin", "test-admin@example.test")
        self.assertFalse((self.root / ".local/users/test-admin.json").exists())
        pending = list((self.root / ".local/users").glob("test-admin.pending-*"))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].stat().st_mode & 0o777, 0o600)
