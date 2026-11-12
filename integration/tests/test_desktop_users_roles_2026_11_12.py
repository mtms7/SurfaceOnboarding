"""2026-11-12 phase 1: desktop users and roles (admin / operator / viewer), the admin Users page and its change log."""
from __future__ import annotations

import contextvars
import io
import json
import os
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import users_file

ADMIN = "owner@example.com"
OPERATOR = "teammate@example.com"
VIEWER = "reader@example.com"
ROOT = Path(__file__).resolve().parents[2]


class UsersFileRulesTests(unittest.TestCase):
    def test_admin_role_parses_and_old_files_still_parse(self):
        self.assertEqual(users_file.parse_users(f"{ADMIN} admin\n{OPERATOR} operator\n{VIEWER}\n"),
                         {ADMIN: "admin", OPERATOR: "operator", VIEWER: "viewer"})
        with self.assertRaises(users_file.UsersFileError):
            users_file.parse_users(f"{ADMIN} owner\n")

    def test_format_round_trips_and_lists_admins_first(self):
        users = {VIEWER: "viewer", OPERATOR: "operator", ADMIN: "admin"}
        text = users_file.format_users(users)
        self.assertEqual(users_file.parse_users(text), users)
        body = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(body, [f"{ADMIN} admin", f"{OPERATOR} operator", VIEWER])

    def test_changes_and_their_refusals(self):
        users = {ADMIN: "admin"}
        added = users_file.apply_change(users, "add", " Teammate@Example.com ", "operator")
        self.assertEqual(added, {ADMIN: "admin", OPERATOR: "operator"})
        self.assertEqual(users, {ADMIN: "admin"})  # never modified in place
        self.assertEqual(users_file.apply_change(added, "set", OPERATOR, "viewer")[OPERATOR], "viewer")
        self.assertNotIn(OPERATOR, users_file.apply_change(added, "remove", OPERATOR))
        for args, reason in (((users, "add", ADMIN, "operator"), "already_listed"),
                             ((users, "add", "not-an-email", "operator"), "invalid_email"),
                             ((users, "add", OPERATOR, "owner"), "invalid_role"),
                             ((users, "set", OPERATOR, "viewer"), "not_listed"),
                             ((users, "set", ADMIN, "admin"), "no_change"),
                             ((users, "set", ADMIN, "operator"), "last_admin"),
                             ((users, "remove", ADMIN, None), "last_admin"),
                             ((users, "drop", ADMIN, None), "unknown_action")):
            with self.subTest(reason=reason), self.assertRaises(users_file.UserChangeError) as caught:
                users_file.apply_change(*args)
            self.assertEqual(str(caught.exception), reason)
        two_admins = {ADMIN: "admin", OPERATOR: "admin"}
        self.assertEqual(users_file.apply_change(two_admins, "remove", ADMIN), {OPERATOR: "admin"})

    def test_save_and_change_log(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "users.txt"
            users_file.save_users_file(path, {ADMIN: "admin", OPERATOR: "operator"})
            self.assertEqual(users_file.load_users_file(path), {ADMIN: "admin", OPERATOR: "operator"})
            self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(sorted(p.name for p in Path(folder).iterdir()), ["users.txt"])  # no temporary file left
            users_file.append_change(path, ADMIN, "add", OPERATOR, "operator")
            users_file.append_change(path, ADMIN, "remove", OPERATOR, None)
            with users_file.change_log_path(path).open("a", encoding="utf-8") as handle:
                handle.write("not json\n")
            changes = users_file.recent_changes(path)
            self.assertEqual([entry["action"] for entry in changes], ["remove", "add"])
            self.assertEqual(changes[0]["by"], ADMIN)

    def test_admin_may_operate_on_the_vm_too(self):
        self.assertTrue(users_file.may_operate("admin"))
        self.assertTrue(users_file.may_operate("operator"))
        self.assertFalse(users_file.may_operate("viewer"))
        self.assertFalse(users_file.may_operate(None))


class UsersFileSandbox(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "dashboard-users.txt"
        users_file.save_users_file(self.path, {ADMIN: "admin", OPERATOR: "operator", VIEWER: "viewer"})
        env = patch.dict(os.environ, {dashboard.USERS_FILE_ENV: str(self.path), "SURFACE_ONBOARDING_RUNTIME": "desktop"})
        env.start()
        self.addCleanup(env.stop)
        for name in (dashboard.ALLOWED_USERS_ENV, dashboard.ALLOWED_USERS_FILE_ENV, dashboard.OPERATORS_ENV,
                     dashboard.OPERATORS_FILE_ENV):
            os.environ.pop(name, None)
        self.tokens: list[str] = []
        self.addCleanup(self._drop_sessions)

    def _drop_sessions(self):
        with dashboard._login_lock:
            for token in self.tokens:
                dashboard._dashboard_sessions.pop(token, None)

    def session(self, email: str) -> str:
        token = "test-" + email + "-" + str(len(self.tokens))
        with dashboard._login_lock:
            dashboard._dashboard_sessions[token] = (dashboard.monotonic() + 600, email)
        self.tokens.append(token)
        return dashboard.SESSION_COOKIE + "=" + token


class DesktopRoleTests(UsersFileSandbox):
    def test_roles_and_allow_list_come_from_the_file(self):
        self.assertEqual(dashboard.desktop_allowed_operators(), frozenset({ADMIN, OPERATOR, VIEWER}))
        self.assertEqual(dashboard.desktop_role(ADMIN.upper()), "admin")
        self.assertEqual(dashboard.desktop_role(VIEWER), "viewer")
        self.assertIsNone(dashboard.desktop_role("stranger@example.com"))
        self.assertIsNone(dashboard.desktop_role(None))

    def test_without_a_file_the_old_allow_list_and_full_access_apply(self):
        with patch.dict(os.environ, {"SURFACE_DASHBOARD_ALLOWED_USERS": ADMIN}):
            os.environ.pop(dashboard.USERS_FILE_ENV)
            self.assertIsNone(dashboard.desktop_users())
            self.assertEqual(dashboard.desktop_allowed_operators(), frozenset({ADMIN}))
            self.assertEqual(dashboard.desktop_role("Signed In"), "admin")

    def test_a_malformed_file_admits_nobody(self):
        self.path.write_text(f"{ADMIN} owner\n", encoding="utf-8")
        self.assertEqual(dashboard.desktop_allowed_operators(), frozenset())
        self.assertIsNone(dashboard.desktop_role(ADMIN))

    def test_a_removed_user_is_signed_out_at_once(self):
        cookie = self.session(OPERATOR)
        self.assertEqual(dashboard.dashboard_operator(cookie), OPERATOR)
        users_file.save_users_file(self.path, {ADMIN: "admin"})
        self.assertIsNone(dashboard.dashboard_operator(cookie))
        users_file.save_users_file(self.path, {ADMIN: "admin", OPERATOR: "operator"})
        self.assertIsNone(dashboard.dashboard_operator(cookie))  # the session is gone, not just hidden

    def test_the_vm_treats_an_admin_as_an_operator(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}):
            self.assertEqual(dashboard.vm_role(ADMIN), "operator")
            self.assertEqual(dashboard.vm_role(VIEWER), "viewer")

    def test_admin_routes_are_post_routes_the_vm_refuses(self):
        self.assertIn(dashboard.ADMIN_USERS_CHANGE_PATH, dashboard.POST_ROUTES)
        self.assertNotIn(dashboard.ADMIN_USERS_CHANGE_PATH, dashboard.VM_OPERATOR_POSTS)


class RouteTests(UsersFileSandbox):
    def handler(self, method: str, path: str, cookie: str, body: bytes = b"") -> dashboard.Handler:
        lines = [f"{method} {path} HTTP/1.1".encode(), b"Host: 127.0.0.1:8765", b"Cookie: " + cookie.encode()]
        if method == "POST":
            lines += [b"Content-Type: application/x-www-form-urlencoded", b"Content-Length: " + str(len(body)).encode(),
                      b"Origin: http://127.0.0.1:8765"]
        raw = b"\r\n".join(lines) + b"\r\n\r\n" + body
        instance = dashboard.Handler.__new__(dashboard.Handler)
        instance.rfile, instance.wfile = io.BytesIO(raw), io.BytesIO()
        instance.client_address = ("127.0.0.1", 50000)
        instance.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
        instance.close_connection = True
        instance.raw_requestline = instance.rfile.readline()
        self.assertTrue(instance.parse_request())
        return instance

    def run_request(self, method: str, path: str, email: str, body: bytes = b"") -> bytes:
        instance = self.handler(method, path, self.session(email), body)
        # A copied context, as each server thread has its own: do_GET sets the signed-in operator, which must not leak
        # into later tests' pages.
        with patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "_session_chip", return_value=""):
            contextvars.copy_context().run(instance.do_POST if method == "POST" else instance.do_GET)
        return instance.wfile.getvalue()

    def status(self, response: bytes) -> int:
        return int(response.split(b" ", 2)[1])

    def test_a_viewer_cannot_start_anything(self):
        with patch.object(dashboard, "post_form") as form:
            response = self.run_request("POST", "/attended/validate", VIEWER, b"reference=CO-0767")
        form.assert_not_called()
        self.assertEqual(self.status(response), 403)
        self.assertIn(b"Read-only access", response)

    def test_only_an_admin_opens_or_changes_users(self):
        self.assertEqual(self.status(self.run_request("GET", "/admin/users", OPERATOR)), 403)
        response = self.run_request("POST", "/admin/users/change", OPERATOR,
                                    b"action=add&email=new%40example.com&role=operator")
        self.assertEqual(self.status(response), 403)
        self.assertNotIn("new@example.com", users_file.load_users_file(self.path))
        page = self.run_request("GET", "/admin/users", ADMIN)
        self.assertEqual(self.status(page), 200)
        self.assertIn(OPERATOR.encode(), page)
        self.assertIn(b"Add user", page)

    def test_an_admin_adds_changes_and_removes_with_a_log(self):
        response = self.run_request("POST", "/admin/users/change", ADMIN,
                                    b"action=add&email=New%40Example.com&role=operator")
        self.assertEqual(self.status(response), 303)
        self.assertIn(b"Location: /admin/users?result=saved", response)
        self.assertEqual(users_file.load_users_file(self.path)["new@example.com"], "operator")
        self.run_request("POST", "/admin/users/change", ADMIN, b"action=set&email=new%40example.com&role=viewer")
        self.assertEqual(users_file.load_users_file(self.path)["new@example.com"], "viewer")
        self.run_request("POST", "/admin/users/change", ADMIN, b"action=remove&email=new%40example.com")
        self.assertNotIn("new@example.com", users_file.load_users_file(self.path))
        log = [json.loads(line) for line in users_file.change_log_path(self.path).read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(entry["by"], entry["action"]) for entry in log], [(ADMIN, "add"), (ADMIN, "set"), (ADMIN, "remove")])

    def test_the_last_admin_cannot_remove_themself(self):
        response = self.run_request("POST", "/admin/users/change", ADMIN, b"action=remove&email=owner%40example.com")
        self.assertIn(b"result=last_admin", response)
        self.assertEqual(users_file.load_users_file(self.path)[ADMIN], "admin")
        self.assertFalse(users_file.change_log_path(self.path).exists())


class LauncherTests(unittest.TestCase):
    def test_launcher_creates_the_users_file_with_the_first_admin(self):
        text = (ROOT / "tools" / "start_attended_dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$UsersFile = ''", text)
        self.assertIn("SurfaceOnboarding\\dashboard-users.txt", text)
        self.assertIn("$env:SURFACE_ONBOARDING_USERS_FILE = $UsersFile", text)
        self.assertIn("UTF8Encoding($false)", text)
        self.assertIn("$firstAdmin admin", text)
        self.assertIn("if (-not (Test-Path -LiteralPath $UsersFile))", text)  # an existing file is never overwritten


if __name__ == "__main__":
    unittest.main()
