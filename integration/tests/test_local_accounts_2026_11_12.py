"""2026-11-12 phase 1b: local dashboard accounts, encrypted store, chained audit log, Salesforce data-user check."""
from __future__ import annotations

import argparse
import contextvars
import io
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from integration.onboarding import secure_store
except ImportError:  # pragma: no cover - CI installs cryptography
    secure_store = None

import tools.dashboard_accounts as setup_cli
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import accounts
from integration.onboarding.session_readiness import SessionState, SessionStatus

SUPER = "mmachados"
OPERATOR = "jostrander"
VIEWER = "reader"
GOOD = "correct-horse-battery"
TEMP = "temporary-pass-123"


@unittest.skipIf(secure_store is None, "cryptography is not installed")
class SecureStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.store = secure_store.SecureStore(self.folder)

    def test_accounts_round_trip_encrypted_with_a_32_byte_key(self):
        self.store.save_accounts({"version": 1, "accounts": {"x": {"role": "viewer"}}})
        self.assertEqual(len(self.store.key_path.read_bytes()), 32)
        raw = (self.folder / secure_store.ACCOUNTS_FILE).read_bytes()
        self.assertNotIn(b"viewer", raw)
        self.assertEqual(secure_store.SecureStore(self.folder).load_accounts()["accounts"]["x"]["role"], "viewer")

    def test_wrong_key_or_tampering_fails_closed(self):
        self.store.save_accounts({"version": 1, "accounts": {}})
        path = self.folder / secure_store.ACCOUNTS_FILE
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 1
        path.write_bytes(bytes(blob))
        with self.assertRaises(secure_store.StoreError):
            self.store.load_accounts()
        other = Path(tempfile.mkdtemp()) / "other.key"
        self.addCleanup(lambda: (other.unlink(missing_ok=True), other.parent.rmdir()))
        self.store.save_accounts({"version": 1, "accounts": {}})
        with self.assertRaises(secure_store.StoreError):
            secure_store.SecureStore(self.folder, other).load_accounts()

    def test_audit_is_encrypted_chained_and_tamper_evident(self):
        for number in range(4):
            self.store.append_audit("action", "someone", "operator", "/attended/x", "CO-07%02d" % number, "200")
        raw = (self.folder / secure_store.AUDIT_FILE).read_bytes()
        self.assertNotIn(b"someone", raw)
        self.assertTrue(self.store.verify_audit().ok)
        self.assertEqual([entry["target"] for entry in self.store.read_audit(2)], ["CO-0703", "CO-0702"])
        self.assertEqual(len(self.store.read_audit(kind="action", user="nobody")), 0)
        lines = raw.splitlines()
        (self.folder / secure_store.AUDIT_FILE).write_bytes(b"\n".join(lines[:1] + lines[2:]) + b"\n")
        check = secure_store.SecureStore(self.folder).verify_audit()
        self.assertEqual((check.ok, check.broken_at, check.reason), (False, 2, "chain_broken"))
        (self.folder / secure_store.AUDIT_FILE).write_bytes(b"\n".join([lines[0], b"bm90LWEtYmxvYg==", lines[1]]) + b"\n")
        self.assertEqual(secure_store.SecureStore(self.folder).verify_audit().reason, "unreadable")

    def test_a_new_store_object_continues_the_chain(self):
        self.store.append_audit("sign_in", "a")
        secure_store.SecureStore(self.folder).append_audit("sign_out", "a")
        self.assertTrue(secure_store.SecureStore(self.folder).verify_audit().ok)


class AccountRulesTests(unittest.TestCase):
    def document(self):
        document = {"version": 1, "accounts": {}}
        accounts.create_superadmin(document, SUPER, GOOD, now=1000.0)
        return document

    def test_hashes_never_contain_the_password(self):
        stored = accounts.hash_password(GOOD)
        self.assertNotIn(GOOD, stored)
        self.assertTrue(accounts.check_password(GOOD, stored))
        self.assertFalse(accounts.check_password(GOOD + "x", stored))
        self.assertFalse(accounts.check_password(GOOD, "garbage"))

    def test_password_rules(self):
        self.assertEqual(accounts.password_problem("short", "x"), "password_too_short")
        self.assertEqual(accounts.password_problem(" leading-space-pass", "x"), "password_edge_spaces")
        self.assertEqual(accounts.password_problem("mmachados-is-me-123", SUPER), "password_contains_username")
        self.assertIsNone(accounts.password_problem(GOOD, SUPER))

    def test_only_one_superadmin_and_it_is_protected(self):
        document = self.document()
        with self.assertRaises(accounts.AccountError) as caught:
            accounts.create_superadmin(document, "another", GOOD)
        self.assertEqual(str(caught.exception), "superadmin_exists")
        for call in (lambda: accounts.set_role(document, SUPER, "viewer"), lambda: accounts.remove_account(document, SUPER),
                     lambda: accounts.reset_password(document, SUPER, TEMP),
                     lambda: accounts.set_disabled(document, SUPER, True)):
            with self.assertRaises(accounts.AccountError) as caught:
                call()
            self.assertEqual(str(caught.exception), "superadmin_protected")
        with self.assertRaises(accounts.AccountError):
            accounts.add_account(document, "x2", accounts.ROLE_SUPERADMIN, TEMP)

    def test_managed_accounts_lifecycle(self):
        document = self.document()
        accounts.add_account(document, "JOstrander", "operator", TEMP)
        self.assertTrue(document["accounts"][OPERATOR]["must_change"])
        accounts.set_role(document, OPERATOR, "viewer")
        accounts.set_disabled(document, OPERATOR, True)
        self.assertEqual(accounts.authenticate(document, OPERATOR, TEMP), (None, "disabled"))
        accounts.set_disabled(document, OPERATOR, False)
        self.assertEqual(accounts.authenticate(document, OPERATOR, TEMP), (OPERATOR, "ok"))
        accounts.change_own_password(document, OPERATOR, TEMP, GOOD)
        self.assertFalse(document["accounts"][OPERATOR]["must_change"])
        accounts.reset_password(document, OPERATOR, TEMP)
        self.assertTrue(document["accounts"][OPERATOR]["must_change"])
        accounts.remove_account(document, OPERATOR)
        self.assertNotIn(OPERATOR, document["accounts"])
        self.assertTrue(all("hash" not in row for row in accounts.public_view(document)))

    def test_lockout_after_five_wrong_passwords(self):
        document = self.document()
        for _attempt in range(4):
            self.assertEqual(accounts.authenticate(document, SUPER, "wrong-password-xx", now=2000.0), (None, "invalid"))
        self.assertEqual(accounts.authenticate(document, SUPER, "wrong-password-xx", now=2000.0), (None, "locked"))
        self.assertEqual(accounts.authenticate(document, SUPER, GOOD, now=2000.0 + 60), (None, "locked"))
        self.assertEqual(accounts.authenticate(document, SUPER, GOOD, now=2000.0 + accounts.LOCK_SECONDS + 1),
                         (SUPER, "ok"))
        self.assertEqual(accounts.authenticate(document, "nobody", GOOD), (None, "invalid"))


@unittest.skipIf(secure_store is None, "cryptography is not installed")
class SetupCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.args = argparse.Namespace(dir=folder.name, key=None, username=SUPER)
        self.folder = Path(folder.name)

    def test_creates_one_superadmin_from_a_hidden_prompt_and_refuses_a_second(self):
        answers = iter(["short", "short", GOOD, "different-password", GOOD, GOOD])
        with patch("builtins.print"):
            self.assertEqual(setup_cli.init_superadmin(self.args, prompt=lambda _text: next(answers)), 0)
        store = secure_store.SecureStore(self.folder)
        self.assertEqual(accounts.superadmin_name(store.load_accounts()), SUPER)
        self.assertEqual(store.read_audit()[0]["action"], "create_superadmin")
        for path in self.folder.iterdir():
            self.assertNotIn(GOOD.encode(), path.read_bytes())
        with patch("builtins.print"):
            self.assertEqual(setup_cli.init_superadmin(self.args, prompt=lambda _text: GOOD), 1)


@unittest.skipIf(secure_store is None, "cryptography is not installed")
class LocalModeSandbox(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = patch.dict(os.environ, {dashboard.ACCOUNTS_DIR_ENV: str(self.folder), "SURFACE_ONBOARDING_RUNTIME": "desktop"})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(dashboard.ACCOUNTS_KEY_ENV, None)
        os.environ.pop(dashboard.USERS_FILE_ENV, None)
        dashboard._store_cache.clear()
        self.addCleanup(dashboard._store_cache.clear)
        self.store = dashboard.account_store()
        document = {"version": 1, "accounts": {}}
        accounts.create_superadmin(document, SUPER, GOOD)
        accounts.add_account(document, OPERATOR, "operator", TEMP)
        accounts.add_account(document, VIEWER, "viewer", TEMP)
        for name in (OPERATOR, VIEWER):
            document["accounts"][name]["must_change"] = False
        self.store.save_accounts(document)
        self.tokens: list[str] = []
        self.addCleanup(self._drop)
        for name, value in (("_session_chip", ""),):
            patcher = patch.object(dashboard, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        probe = patch.object(dashboard, "probe_salesforce_readiness")
        self.probe = probe.start()
        self.addCleanup(probe.stop)

    def _drop(self):
        with dashboard._login_lock:
            for token in self.tokens:
                dashboard._dashboard_sessions.pop(token, None)

    def session(self, name: str) -> str:
        token = "local-test-" + name + "-" + str(len(self.tokens))
        with dashboard._login_lock:
            dashboard._dashboard_sessions[token] = (dashboard.monotonic() + 600, name)
        self.tokens.append(token)
        return dashboard.SESSION_COOKIE + "=" + token

    def request(self, method: str, path: str, cookie: str = "", body: bytes = b"") -> bytes:
        lines = [f"{method} {path} HTTP/1.1".encode(), b"Host: 127.0.0.1:8765"]
        if cookie:
            lines.append(b"Cookie: " + cookie.encode())
        if method == "POST":
            lines += [b"Content-Type: application/x-www-form-urlencoded", b"Content-Length: " + str(len(body)).encode(),
                      b"Origin: http://127.0.0.1:8765"]
        raw = b"\r\n".join(lines) + b"\r\n\r\n" + body
        handler = dashboard.Handler.__new__(dashboard.Handler)
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.client_address = ("127.0.0.1", 50000)
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765))
        handler.close_connection = True
        handler.raw_requestline = handler.rfile.readline()
        self.assertTrue(handler.parse_request())
        contextvars.copy_context().run(handler.do_POST if method == "POST" else handler.do_GET)
        return handler.wfile.getvalue()

    @staticmethod
    def status(response: bytes) -> int:
        return int(response.split(b" ", 2)[1])

    @staticmethod
    def location(response: bytes) -> str:
        for line in response.split(b"\r\n"):
            if line.lower().startswith(b"location:"):
                return line.split(b":", 1)[1].strip().decode()
        return ""


class LocalLoginTests(LocalModeSandbox):
    def test_login_page_is_the_username_form(self):
        page = self.request("GET", "/login")
        self.assertEqual(self.status(page), 200)
        self.assertIn(b"name='username'", page)
        self.assertIn(b"type='password'", page)
        self.assertNotIn(b"Sign in with Salesforce", page)

    def test_wrong_then_right_password_with_audit(self):
        wrong = self.request("POST", "/login/password", body=b"username=mmachados&password=wrong-password-xx")
        self.assertEqual(self.location(wrong), "/login?reason=invalid")
        right = self.request("POST", "/login/password", body=("username=mmachados&password=" + GOOD).encode())
        self.assertEqual((self.status(right), self.location(right)), (303, "/"))
        self.assertIn(dashboard.SESSION_COOKIE.encode() + b"=", right)
        kinds = [entry["kind"] for entry in self.store.read_audit()]
        self.assertEqual(kinds[:2], ["sign_in", "sign_in_failed"])
        self.probe.assert_called()  # Salesforce is only checked, separately
        for path in self.folder.iterdir():
            self.assertNotIn(GOOD.encode(), path.read_bytes())

    def test_salesforce_sign_in_routes_no_longer_sign_anyone_in(self):
        with patch.object(dashboard, "start_dashboard_login") as start:
            response = self.request("POST", "/login/start")
        start.assert_not_called()
        self.assertEqual(self.location(response), "/login")

    def test_a_temporary_password_must_be_changed_first(self):
        document = self.store.load_accounts()
        document["accounts"][OPERATOR]["must_change"] = True
        self.store.save_accounts(document)
        cookie = self.session(OPERATOR)
        self.assertEqual(self.location(self.request("GET", "/", cookie)), "/account/password")
        self.assertEqual(self.location(self.request("POST", "/attended/validate", cookie, b"reference=CO-0767")),
                         "/account/password")
        mismatch = self.request("POST", "/account/password", cookie,
                                ("current=" + TEMP + "&new=" + GOOD + "&confirm=other-password-12").encode())
        self.assertIn("password_mismatch", self.location(mismatch))
        done = self.request("POST", "/account/password", cookie, ("current=" + TEMP + "&new=" + GOOD + "&confirm="
                                                                  + GOOD).encode())
        self.assertEqual(self.location(done), "/")
        self.assertFalse(self.store.load_accounts()["accounts"][OPERATOR]["must_change"])

    def test_sign_out_keeps_the_salesforce_session_and_is_audited(self):
        cookie = self.session(OPERATOR)
        with patch.object(dashboard, "_sf_quiet") as quiet, \
                patch.object(dashboard, "any_run_in_progress", return_value=False), \
                patch.object(dashboard, "close_kept_production_window"):
            response = self.request("POST", "/logout", cookie)
        quiet.assert_not_called()
        self.assertEqual(self.location(response), "/login")
        self.assertEqual(self.store.read_audit(1)[0]["kind"], "sign_out")


class LocalRolesAndAuditTests(LocalModeSandbox):
    def test_viewer_cannot_act_and_operator_cannot_manage_users(self):
        response = self.request("POST", "/attended/validate", self.session(VIEWER), b"reference=CO-0767")
        self.assertEqual(self.status(response), 403)
        self.assertEqual(self.status(self.request("GET", "/admin/users", self.session(OPERATOR))), 403)
        refused = self.request("POST", "/admin/users/change", self.session(OPERATOR),
                               ("action=add&username=newbie&role=operator&temporary_password=" + TEMP).encode())
        self.assertEqual(self.status(refused), 403)
        self.assertNotIn("newbie", self.store.load_accounts()["accounts"])

    def test_superadmin_page_and_sidebar(self):
        page = self.request("GET", "/admin/users", self.session(SUPER))
        self.assertEqual(self.status(page), 200)
        for text in (b"Accounts", b"Add a user", b"Activity", b"chain verified", b"op-name", b"href='/admin/users'",
                     OPERATOR.encode()):
            self.assertIn(text, page)
        self.assertNotIn(b"scrypt$", page)
        operator_page = self.request("GET", "/account/password", self.session(OPERATOR))
        self.assertNotIn(b"href='/admin/users'", operator_page)
        self.assertIn(b"Change password", operator_page)

    def test_superadmin_manages_accounts_and_every_change_is_audited_without_passwords(self):
        cookie = self.session(SUPER)
        add = self.request("POST", "/admin/users/change", cookie,
                           ("action=add&username=EMendel&role=operator&temporary_password=" + TEMP).encode())
        self.assertEqual(self.location(add), "/admin/users?result=saved")
        self.assertTrue(self.store.load_accounts()["accounts"]["emendel"]["must_change"])
        newbie = self.session("emendel")
        self.request("POST", "/admin/users/change", cookie, b"action=disable&username=emendel")
        self.assertIsNone(dashboard.dashboard_operator(newbie))  # disabled: signed out at once
        self.request("POST", "/admin/users/change", cookie, b"action=remove&username=emendel")
        self.assertNotIn("emendel", self.store.load_accounts()["accounts"])
        protected = self.request("POST", "/admin/users/change", cookie, b"action=remove&username=mmachados")
        self.assertIn("superadmin_protected", self.location(protected))
        entries = self.store.read_audit()
        self.assertEqual([entry["action"] for entry in entries if entry["kind"] == "account"][:4],
                         ["remove", "remove", "disable", "add"])
        self.assertTrue(all(TEMP not in str(entry) for entry in entries))

    def test_every_action_is_audited_with_its_co(self):
        cookie = self.session(OPERATOR)
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(dashboard, "USER_CREATED_CONFIRMATION_PATH", Path(folder) / "confirm.json"):
            response = self.request("POST", "/attended/confirm-user-created", cookie, b"reference=CO-0767")
        self.assertEqual(self.status(response), 303)
        entry = self.store.read_audit(1)[0]
        self.assertEqual((entry["kind"], entry["user"], entry["role"], entry["action"], entry["target"], entry["result"]),
                         ("action", OPERATOR, "operator", "/attended/confirm-user-created", "CO-0767", "303"))
        self.assertTrue(self.store.verify_audit().ok)

    def test_a_broken_store_lets_nobody_in(self):
        (self.folder / "accounts.enc").write_bytes(b"SOS1" + b"\0" * 40)
        dashboard._store_cache.clear()
        self.assertIsNone(dashboard.dashboard_operator(self.session(SUPER)))
        response = self.request("POST", "/login/password", body=("username=mmachados&password=" + GOOD).encode())
        self.assertEqual(self.location(response), "/login?reason=store_unavailable")


class SalesforceDataUserTests(unittest.TestCase):
    def ready(self):
        return SessionStatus(SessionState.READY, "salesforce_ready", datetime(2026, 11, 12))

    def test_only_the_expected_user_keeps_salesforce_ready(self):
        now = datetime(2026, 11, 12)
        with patch.dict(os.environ, {"SURFACE_SF_EXPECTED_DATA_USER": "Owner@Example.com"}), \
                patch.object(dashboard, "_sf_quiet") as quiet:
            with patch.object(dashboard, "salesforce_userinfo", return_value={"email": "owner@example.com"}):
                self.assertIs(dashboard.salesforce_data_user_status(self.ready(), now).state, SessionState.READY)
            with patch.object(dashboard, "salesforce_userinfo", return_value={"email": "someone@example.com"}):
                status = dashboard.salesforce_data_user_status(self.ready(), now)
            self.assertEqual((status.state, status.reason), (SessionState.BLOCKED, "salesforce_wrong_user"))
            quiet.assert_called_once()
            with patch.object(dashboard, "salesforce_userinfo", return_value=None):
                self.assertEqual(dashboard.salesforce_data_user_status(self.ready(), now).reason,
                                 "salesforce_user_unverified")
        blocked = SessionStatus(SessionState.BLOCKED, "salesforce_wrong_org", now)
        with patch.dict(os.environ, {"SURFACE_SF_EXPECTED_DATA_USER": "owner@example.com"}):
            self.assertIs(dashboard.salesforce_data_user_status(blocked, now), blocked)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SURFACE_SF_EXPECTED_DATA_USER", None)
            self.assertIs(dashboard.salesforce_data_user_status(self.ready(), now).state, SessionState.READY)


class LauncherTests(unittest.TestCase):
    def test_launcher_sets_the_store_and_the_expected_salesforce_user(self):
        text = (Path(__file__).resolve().parents[2] / "tools" / "start_attended_dashboard.ps1").read_text(encoding="utf-8")
        self.assertIn("SurfaceOnboarding\\accounts", text)
        self.assertIn("$env:SURFACE_DASHBOARD_ACCOUNTS_DIR = $AccountsDir", text)
        self.assertIn("$env:SURFACE_SF_EXPECTED_DATA_USER = $AllowedUser", text)
        self.assertIn("tools/dashboard_accounts.py init-superadmin", text)


if __name__ == "__main__":
    unittest.main()
