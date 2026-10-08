"""VM team-access mode (docs/40 step 0): proxy identity, allow-list and roles, HTTPS origin checks, Secure cookies,
one state folder. Every VM behaviour is only on with SURFACE_ONBOARDING_RUNTIME=vm; the desktop is unchanged."""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import leonardo_inventory, state_paths, users_file

REPO = Path(__file__).resolve().parents[2]
OWNER = "milton.stevenson@pentera.io"
VIEWER = "viewer.person@pentera.io"
STRANGER = "stranger@example.com"
PUBLIC = "https://172.26.37.20:8443"
PUBLIC_HOST = "172.26.37.20:8443"
# A made-up test value that exists only in this file; the real secret never touches the repository.
SECRET = "unit-test-proxy-secret-0123456789abcdef-NOT-REAL"
SECRET_HEADER = "X-Surface-Proxy-Secret"
VM_ENV = {
    "SURFACE_ONBOARDING_PROXY_SECRET": SECRET,
    "SURFACE_ONBOARDING_RUNTIME": "vm",
    "SURFACE_ONBOARDING_ALLOWED_USERS": f"{OWNER},{VIEWER}",
    "SURFACE_ONBOARDING_OPERATORS": OWNER,
    "SURFACE_ONBOARDING_PUBLIC_ORIGIN": PUBLIC,
}
# POSTs that start a Leonardo/browser run, sign in, or write Salesforce (or sweep with the runner): never in VM phase 1.
REFUSED_IN_VM_FOR_EVERYONE = sorted(dashboard.POST_ROUTES - dashboard.VM_OPERATOR_POSTS)


def vm_env(**extra):
    return patch.dict(os.environ, {**VM_ENV, **extra})


class ServerCase(unittest.TestCase):
    """Real loopback server so the TCP peer is genuine; returns (status, headers, body)."""

    def request(self, method, path, headers=None, body=b"", host=PUBLIC_HOST, secret=SECRET):
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        connection.putrequest(method, path, skip_host=True)
        merged = {"Host": host, "Content-Length": str(len(body))}
        if secret is not None:
            merged[SECRET_HEADER] = secret  # what nginx adds; pass secret=None to leave it out
        if method == "POST":
            merged["Content-Type"] = "application/x-www-form-urlencoded"
        merged.update(headers or {})
        for name, value in merged.items():
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8", "replace")

    def post(self, path, email, reference="CO-0001", extra=None, body=None):
        headers = {"Origin": PUBLIC, "Referer": PUBLIC + "/co/" + reference}
        if email:
            headers[dashboard.PROXY_IDENTITY_HEADER] = email
        headers.update(extra or {})
        return self.request("POST", path, headers, f"reference={reference}".encode() if body is None else body)


class ProxyIdentityTests(ServerCase):
    def setUp(self):
        patcher = patch.object(dashboard, "render_dashboard", return_value="<html>QUEUE-OK</html>")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_header_name_is_a_constant(self):
        self.assertEqual(dashboard.PROXY_IDENTITY_HEADER, "X-Forwarded-Email")

    def test_pure_identity_rules(self):
        with vm_env():
            self.assertEqual(dashboard.proxy_identity("127.0.0.1", [OWNER.upper()], [SECRET]), OWNER)
            self.assertEqual(dashboard.proxy_identity("::1", [OWNER], [SECRET]), OWNER)
            self.assertIsNone(dashboard.proxy_identity("10.1.2.3", [OWNER], [SECRET]))  # not the proxy
            self.assertIsNone(dashboard.proxy_identity("172.26.37.20", [OWNER], [SECRET]))  # own address is not loopback
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [], [SECRET]))
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER, VIEWER], [SECRET]))  # more than one value
            for bad in ("", "no-at-sign", f"{OWNER}, {VIEWER}", f"{OWNER} x", "<x>@pentera.io", "a@b", "a@@pentera.io"):
                self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [bad], [SECRET]), bad)
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER], [SECRET]))  # desktop never trusts it

    def test_header_ignored_from_a_non_loopback_peer(self):
        with vm_env(), patch.object(dashboard, "TRUSTED_PROXY_PEERS", frozenset({"10.255.255.254"})):
            status, _, body = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
        self.assertEqual(status, 403)
        self.assertNotIn("QUEUE-OK", body)
        self.assertIn("Sign-in required", body)

    def test_header_ignored_in_desktop_mode(self):
        desktop = {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_ALLOWED_USERS": OWNER}
        with patch.dict(os.environ, desktop):
            status, headers, _ = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER}, host="127.0.0.1:1")
        # Host is refused first on the desktop (loopback only); with a loopback Host the page still needs the desktop login.
        self.assertEqual(status, 403)
        with patch.dict(os.environ, desktop), patch.object(dashboard, "request_origin_problem", return_value=None):
            status, headers, body = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")
        self.assertNotIn("QUEUE-OK", body)

    def test_missing_header_is_refused_in_vm_mode(self):
        with vm_env():
            status, _, body = self.request("GET", "/")
        self.assertEqual(status, 403)
        self.assertNotIn("QUEUE-OK", body)

    def test_two_header_values_are_refused(self):
        with vm_env():
            server_headers = {dashboard.PROXY_IDENTITY_HEADER: f"{OWNER}"}
            # http.client can send one header per name only through putheader twice; do it by hand via a raw socket.
            server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            import socket
            with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10) as sock:
                sock.sendall((f"GET / HTTP/1.1\r\nHost: {PUBLIC_HOST}\r\n{dashboard.PROXY_IDENTITY_HEADER}: {OWNER}\r\n"
                              f"{SECRET_HEADER}: {SECRET}\r\n"
                              f"{dashboard.PROXY_IDENTITY_HEADER}: {VIEWER}\r\nConnection: close\r\n\r\n").encode())
                raw = b""
                while chunk := sock.recv(65536):
                    raw += chunk
            self.assertTrue(raw.startswith(b"HTTP/1.0 403") or raw.startswith(b"HTTP/1.1 403"), raw[:40])
            self.assertNotIn(b"QUEUE-OK", raw)
            del server_headers

    def test_allowed_user_sees_the_page_and_unknown_user_gets_403(self):
        with vm_env():
            status, _, body = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
            self.assertEqual((status, "QUEUE-OK" in body), (200, True))
            status, _, body = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: VIEWER})
            self.assertEqual((status, "QUEUE-OK" in body), (200, True))
            status, _, body = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: STRANGER})
            self.assertEqual(status, 403)
            self.assertNotIn("QUEUE-OK", body)
            self.assertIn("allow-list", body)

    def test_empty_allow_list_admits_nobody(self):
        with vm_env(SURFACE_ONBOARDING_ALLOWED_USERS="", SURFACE_ONBOARDING_OPERATORS=OWNER):
            status, _, _ = self.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
        self.assertEqual(status, 403)

    def test_operator_not_on_the_allow_list_is_unknown(self):
        with vm_env(SURFACE_ONBOARDING_ALLOWED_USERS=VIEWER):
            self.assertIsNone(dashboard.vm_role(OWNER))
            self.assertEqual(dashboard.vm_role(VIEWER), "viewer")

    def test_roles_and_files(self):
        with tempfile.TemporaryDirectory() as folder:
            allowed = Path(folder) / "allowed.txt"
            operators = Path(folder) / "operators.txt"
            allowed.write_text(f"# pilot\n{OWNER}\n{VIEWER.upper()}  # viewer\n", encoding="utf-8")
            operators.write_text(f"{OWNER}\n", encoding="utf-8")
            env = {"SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_ALLOWED_USERS": "",
                   "SURFACE_ONBOARDING_ALLOWED_USERS_FILE": str(allowed), "SURFACE_ONBOARDING_OPERATORS": "",
                   "SURFACE_ONBOARDING_OPERATORS_FILE": str(operators)}
            with patch.dict(os.environ, env):
                self.assertEqual(dashboard.vm_role(OWNER), "operator")
                self.assertEqual(dashboard.vm_role(VIEWER), "viewer")
                self.assertIsNone(dashboard.vm_role(STRANGER))
                self.assertIsNone(dashboard.vm_role(None))
            with patch.dict(os.environ, {**env, "SURFACE_ONBOARDING_ALLOWED_USERS_FILE": str(Path(folder) / "missing")}):
                self.assertIsNone(dashboard.vm_role(OWNER))  # unreadable file fails closed

    def test_users_template_lists_only_the_owner_as_operator(self):
        text = (REPO / "integration" / "deployment" / "vm_pilot" / "users.txt.template").read_text(encoding="utf-8")
        self.assertEqual(users_file.parse_users(text), {OWNER: "operator"})

    def test_john_is_not_preconfigured(self):
        text = (REPO / "integration" / "deployment" / "vm_pilot" / "allowed-emails.txt.template").read_text(encoding="utf-8")
        entries = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(entries, [OWNER])

    def test_desktop_login_routes_stay_refused_in_vm_mode(self):
        with vm_env(), patch.object(dashboard, "start_dashboard_login", side_effect=AssertionError("must not run")):
            status, _, _ = self.request("GET", "/login", {dashboard.PROXY_IDENTITY_HEADER: OWNER})
            self.assertEqual(status, 503)
            for path in ("/login/start", "/login/cancel"):
                status, _, _ = self.post(path, OWNER)
                self.assertEqual(status, 503, path)
            # a viewer is refused before anything else
            self.assertEqual(self.post("/login/start", VIEWER)[0], 403)


class VmPostPolicyTests(ServerCase):
    def test_viewer_gets_403_on_every_post(self):
        with vm_env(), patch.object(dashboard, "set_user_created_confirmation", side_effect=AssertionError("must not run")):
            for path in sorted(dashboard.POST_ROUTES) + ["/login/start", "/logout", "/attended/unknown"]:
                status, _, _ = self.post(path, VIEWER)
                self.assertEqual(status, 403, path)

    def test_unknown_and_missing_identity_get_403_on_every_post(self):
        with vm_env():
            for path in sorted(dashboard.POST_ROUTES):
                self.assertEqual(self.post(path, STRANGER)[0], 403, path)
                self.assertEqual(self.post(path, None)[0], 403, path)

    def test_run_starting_posts_are_refused_for_operators_too(self):
        guards = ["start_attended_ce_only_runner", "start_attended_surface_runner", "start_attended_renewal_runner",
                  "start_attended_validation", "start_attended_validation_all", "start_attended_scan_status",
                  "start_attended_scan_status_all", "start_attended_inventory_export", "start_attended_spycloud_check",
                  "open_attended_leonardo_tenant_management", "open_attended_production_backoffice_login",
                  "_start_runner_mode", "_claim_and_launch", "write_salesforce_id_after_confirmation",
                  "update_co0741_comment_after_confirmation", "reset_runner_record", "bootstrap_leonardo_session",
                  "check_leonardo_session", "reset_leonardo_profile", "close_automation_browser",
                  "start_attended_salesforce_login", "prepare_sessions", "record_runner_start"]
        stack = [patch.object(dashboard, name, side_effect=AssertionError(name + " must not run")) for name in guards
                 if hasattr(dashboard, name)]
        self.assertGreaterEqual(len(stack), 15)
        for path in ("/attended/start-ce-only-runner", "/attended/start-surface-runner", "/attended/start-renewal",
                     "/attended/validate", "/attended/scan-status-refresh-all", "/attended/inventory-refresh",
                     "/attended/salesforce-id-writeback-confirm", "/attended/confirm-comment-update",
                     "/attended/reset-ce-only-runner", "/attended/leonardo-dev-session-bootstrap",
                     "/attended/prepare-sessions", "/attended/salesforce-login"):
            self.assertIn(path, REFUSED_IN_VM_FOR_EVERYONE)
        with vm_env():
            for patcher in stack:
                patcher.start()
                self.addCleanup(patcher.stop)
            for path in REFUSED_IN_VM_FOR_EVERYONE:
                status, _, body = self.post(path, OWNER)
                self.assertIn(status, (403, 503), path)
                if path not in ("/login/start", "/login/cancel"):
                    self.assertEqual(status, 403, path)
                    self.assertIn("Nothing was started", body)
            for path in ("/logout", "/attended/unknown"):
                self.assertEqual(self.post(path, OWNER)[0], 403, path)

    def test_no_vm_post_is_permitted_for_any_role_in_phase_1(self):
        # Owner decision 2026-10-08: the VM is a read-only snapshot viewer; operators have no write path either.
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())

    def test_operator_confirmation_is_refused_and_writes_nothing_on_the_vm(self):
        with tempfile.TemporaryDirectory() as folder, vm_env(), \
                patch.object(dashboard, "USER_CREATED_CONFIRMATION_PATH", Path(folder) / "confirmations.json"):
            for path in ("/attended/confirm-user-created", "/attended/unconfirm-user-created"):
                status, _, body = self.post(path, OWNER, "CO-0123")
                self.assertEqual(status, 403, path)
                self.assertIn("Nothing was started", body)
            self.assertFalse((Path(folder) / "confirmations.json").exists())

    def test_desktop_post_behaviour_is_unchanged(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}), \
                patch.object(dashboard, "request_origin_problem", return_value=None):
            status, _, _ = self.request("POST", "/attended/confirm-user-created", {}, b"", host="127.0.0.1:1")
        self.assertEqual(status, 403)  # not signed in on the desktop, as before
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}), \
                patch.object(dashboard, "request_origin_problem", return_value=None), \
                patch.object(dashboard, "login_required", return_value=False), \
                patch.object(dashboard, "set_user_created_confirmation") as record:
            status, _, _ = self.request("POST", "/attended/confirm-user-created", {}, b"reference=CO-0123")
        self.assertEqual(status, 303)
        record.assert_called_once_with("CO-0123", True, None)


class ProxySecretTests(ServerCase):
    """docs/40 step 0 follow-up 1: a shared secret on top of the loopback check."""

    def setUp(self):
        patcher = patch.object(dashboard, "render_dashboard", return_value="<html>QUEUE-OK</html>")
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, secret=SECRET, extra=None, email=OWNER):
        headers = {dashboard.PROXY_IDENTITY_HEADER: email, **(extra or {})}
        return self.request("GET", "/", headers, secret=secret)

    def raw_get(self, *header_lines):
        import socket
        server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        lines = "".join(line + "\r\n" for line in header_lines)
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=10) as sock:
            sock.sendall(f"GET / HTTP/1.1\r\nHost: {PUBLIC_HOST}\r\n{lines}Connection: close\r\n\r\n".encode())
            raw = b""
            while chunk := sock.recv(65536):
                raw += chunk
        return raw

    def assert_refused(self, response):
        status, _, body = response
        self.assertEqual(status, 403)
        self.assertNotIn("QUEUE-OK", body)
        self.assertIn("Sign-in required", body)  # same refusal as an invalid identity

    def test_header_and_env_names(self):
        self.assertEqual(dashboard.PROXY_SECRET_HEADER, SECRET_HEADER)
        self.assertEqual(dashboard.PROXY_SECRET_ENV, "SURFACE_ONBOARDING_PROXY_SECRET")
        self.assertEqual(dashboard.PROXY_SECRET_FILE_ENV, "SURFACE_ONBOARDING_PROXY_SECRET_FILE")

    def test_correct_secret_loopback_and_allow_listed_email_works(self):
        with vm_env():
            status, _, body = self.get()
        self.assertEqual((status, "QUEUE-OK" in body), (200, True))

    def test_secret_from_a_file_works_and_the_file_wins_over_the_variable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy-secret"
            file_secret = "file-secret-" + "x" * 40
            path.write_text(file_secret + "\n", encoding="utf-8")
            with vm_env(SURFACE_ONBOARDING_PROXY_SECRET_FILE=str(path)):
                self.assertEqual(self.get(secret=file_secret)[0], 200)
                self.assert_refused(self.get(secret=SECRET))  # the variable is ignored when a file is named
            with vm_env(SURFACE_ONBOARDING_PROXY_SECRET_FILE=str(Path(folder) / "missing")):
                self.assert_refused(self.get())  # unreadable file: no fallback to the variable

    def test_missing_secret_header_is_refused(self):
        with vm_env():
            self.assert_refused(self.get(secret=None))

    def test_wrong_secret_is_refused(self):
        with vm_env():
            for wrong in (SECRET[:-1], SECRET + "x", SECRET.upper(), "", " ", "x" * 64):
                self.assert_refused(self.get(secret=wrong))

    def test_missing_or_short_configured_secret_is_refused_even_if_the_client_matches(self):
        for configured in ("", "short", "x" * 31, " " * 40, "has space " + "x" * 40):
            with vm_env(SURFACE_ONBOARDING_PROXY_SECRET=configured):
                self.assert_refused(self.get(secret=configured))
        with vm_env(SURFACE_ONBOARDING_PROXY_SECRET="x" * 32):
            self.assertEqual(self.get(secret="x" * 32)[0], 200)  # 32 is the minimum

    def test_two_secret_header_values_are_refused(self):
        with vm_env():
            raw = self.raw_get(f"{dashboard.PROXY_IDENTITY_HEADER}: {OWNER}", f"{SECRET_HEADER}: {SECRET}",
                               f"{SECRET_HEADER}: {SECRET}")
            self.assertEqual(raw.split(b"\r\n", 1)[0].split()[1], b"403", raw[:40])
            self.assertNotIn(b"QUEUE-OK", raw)
            raw = self.raw_get(f"{dashboard.PROXY_IDENTITY_HEADER}: {OWNER}", f"{SECRET_HEADER}: {SECRET}")
            self.assertEqual(raw.split(b"\r\n", 1)[0].split()[1], b"200")  # sanity: one value is fine

    def test_secret_from_a_non_loopback_peer_is_refused(self):
        with vm_env(), patch.object(dashboard, "TRUSTED_PROXY_PEERS", frozenset({"10.255.255.254"})):
            self.assert_refused(self.get())

    def test_secret_alone_does_not_grant_access_to_an_unknown_user(self):
        with vm_env():
            status, _, body = self.get(email=STRANGER)
        self.assertEqual(status, 403)
        self.assertNotIn("QUEUE-OK", body)

    def test_post_without_the_secret_is_refused_before_any_handler(self):
        with vm_env(), patch.object(dashboard, "set_user_created_confirmation", side_effect=AssertionError("no")):
            status, _, _ = self.request(
                "POST", "/attended/confirm-user-created",
                {"Origin": PUBLIC, dashboard.PROXY_IDENTITY_HEADER: OWNER}, b"reference=CO-0123", secret=None)
        self.assertEqual(status, 403)

    def test_the_secret_never_appears_in_responses_or_logs(self):
        import io
        import logging
        captured = io.StringIO()
        handler = logging.StreamHandler(captured)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        wrong = SECRET[::-1]
        with vm_env(), patch("sys.stdout", new_callable=io.StringIO) as out, \
                patch("sys.stderr", new_callable=io.StringIO) as err:
            responses = [self.get(), self.get(secret=None), self.get(secret=wrong), self.get(email=STRANGER),
                         self.request("GET", "/oauth2/sign_out", {dashboard.PROXY_IDENTITY_HEADER: OWNER}),
                         self.post("/attended/start-surface-runner", OWNER)]
        self.assertEqual(responses[0][0], 200)
        for _, headers, body in responses:
            blob = body + "\n".join(f"{name}: {value}" for name, value in headers.items())
            for needle in (SECRET, wrong):
                self.assertNotIn(needle, blob)
        for text in (captured.getvalue(), out.getvalue(), err.getvalue()):
            for needle in (SECRET, wrong):
                self.assertNotIn(needle, text)
        with vm_env():
            self.assertNotIn(SECRET, repr(dashboard.proxy_identity("127.0.0.1", [OWNER], [SECRET])))

    def test_desktop_mode_is_unaffected_by_the_secret_setting(self):
        desktop = {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_ALLOWED_USERS": OWNER}
        with patch.dict(os.environ, {**desktop, "SURFACE_ONBOARDING_PROXY_SECRET": ""}):
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER], [SECRET]))
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER], None))
            status, _, _ = self.request("GET", "/", {}, host="127.0.0.1:1", secret=None)
            self.assertEqual(status, 403)  # Host refused first on the desktop, exactly as before
            self.assertEqual(dashboard.cookie_attributes(), "; HttpOnly; SameSite=Strict")


class UsersFileParserTests(unittest.TestCase):
    def test_roles_comments_case_and_blank_lines(self):
        text = f"# pilot\n\n{OWNER} operator  # owner\n{VIEWER.upper()}\n  {STRANGER.upper()}   OPERATOR \n"
        self.assertEqual(users_file.parse_users(text),
                         {OWNER: "operator", VIEWER: "viewer", STRANGER: "operator"})
        self.assertEqual(users_file.parse_users("   \n# only comments\n"), {})

    def test_same_line_repeated_is_fine_but_conflicting_roles_are_malformed(self):
        self.assertEqual(users_file.parse_users(f"{OWNER}\n{OWNER.upper()}\n"), {OWNER: "viewer"})
        with self.assertRaises(users_file.UsersFileError):
            users_file.parse_users(f"{OWNER}\n{OWNER} operator\n")

    def test_malformed_lines_are_refused_without_echoing_the_content(self):
        bad_lines = ["not-an-email", f"{OWNER} admin", f"{OWNER} operator extra", f"{OWNER}, {VIEWER}", "a@b",
                     "<x>@pentera.io operator", f"{OWNER}\toperator\tviewer", "operator"]
        for bad in bad_lines:
            with self.assertRaises(users_file.UsersFileError) as caught:
                users_file.parse_users(f"{VIEWER}\n{bad}\n")
            self.assertIn("line 2", str(caught.exception), bad)
            self.assertNotIn("pentera", str(caught.exception), bad)

    def test_load_fails_closed_on_unreadable_binary_or_huge_files(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing"
            binary = Path(folder) / "binary"
            binary.write_bytes(b"\xff\xfe\x00bad")
            huge = Path(folder) / "huge"
            huge.write_text("#" * (users_file.MAX_USERS_FILE_BYTES + 1), encoding="utf-8")
            for path in (missing, binary, huge, Path(folder)):
                with self.assertRaises(users_file.UsersFileError):
                    users_file.load_users_file(path)


class UsersFilePrecedenceTests(unittest.TestCase):
    def write(self, folder, text):
        path = Path(folder) / "users.txt"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def env(self, **extra):
        base = {"SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_ALLOWED_USERS": "",
                "SURFACE_ONBOARDING_ALLOWED_USERS_FILE": "", "SURFACE_ONBOARDING_OPERATORS": "",
                "SURFACE_ONBOARDING_OPERATORS_FILE": "", "SURFACE_ONBOARDING_USERS_FILE": ""}
        return patch.dict(os.environ, {**base, **extra})

    def test_users_file_alone_defines_allow_list_and_roles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, f"{OWNER} operator\n{VIEWER}\n")
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path):
                self.assertEqual(dashboard.vm_role(OWNER), "operator")
                self.assertEqual(dashboard.vm_role(VIEWER.upper()), "viewer")
                self.assertIsNone(dashboard.vm_role(STRANGER))
                self.assertIsNone(dashboard.vm_role(None))

    def test_no_configuration_admits_nobody(self):
        with self.env():
            self.assertIsNone(dashboard.vm_role(OWNER))

    def test_malformed_missing_or_empty_users_file_admits_nobody(self):
        with tempfile.TemporaryDirectory() as folder:
            for text in (f"{OWNER} operator\nbroken line here now\n", f"{OWNER} root\n", "# nobody\n"):
                with self.env(SURFACE_ONBOARDING_USERS_FILE=self.write(folder, text)):
                    self.assertIsNone(dashboard.vm_role(OWNER), text)
            with self.env(SURFACE_ONBOARDING_USERS_FILE=str(Path(folder) / "missing")):
                self.assertIsNone(dashboard.vm_role(OWNER))

    def test_legacy_allow_list_wins_over_the_users_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, f"{OWNER} operator\n{VIEWER}\n")
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path, SURFACE_ONBOARDING_ALLOWED_USERS=STRANGER):
                self.assertEqual(dashboard.vm_role(STRANGER), "viewer")
                self.assertIsNone(dashboard.vm_role(OWNER))  # on the users file only: not admitted
                self.assertIsNone(dashboard.vm_role(VIEWER))
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path, SURFACE_ONBOARDING_ALLOWED_USERS=f"{OWNER},{VIEWER}",
                          SURFACE_ONBOARDING_OPERATORS=VIEWER):
                self.assertEqual(dashboard.vm_role(VIEWER), "operator")  # legacy operators win
                self.assertEqual(dashboard.vm_role(OWNER), "viewer")
            legacy = Path(folder) / "legacy.txt"
            legacy.write_text(STRANGER + "\n", encoding="utf-8")
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path, SURFACE_ONBOARDING_ALLOWED_USERS_FILE=str(legacy)):
                self.assertEqual(dashboard.vm_role(STRANGER), "viewer")
                self.assertIsNone(dashboard.vm_role(OWNER))
            # an unreadable legacy file does not fall back to the users file
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path,
                          SURFACE_ONBOARDING_ALLOWED_USERS_FILE=str(Path(folder) / "missing")):
                self.assertIsNone(dashboard.vm_role(OWNER))

    def test_an_operator_must_still_be_allow_listed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, f"{VIEWER}\n")
            with self.env(SURFACE_ONBOARDING_USERS_FILE=path, SURFACE_ONBOARDING_OPERATORS=OWNER):
                self.assertIsNone(dashboard.vm_role(OWNER))
                self.assertEqual(dashboard.vm_role(VIEWER), "viewer")

    def test_operators_from_the_users_file_still_cannot_start_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write(folder, f"{OWNER} operator\n")
            case = ServerCase()
            case.setUp()
            try:
                with vm_env(SURFACE_ONBOARDING_ALLOWED_USERS="", SURFACE_ONBOARDING_OPERATORS="",
                            SURFACE_ONBOARDING_USERS_FILE=path), \
                        patch.object(dashboard, "start_attended_surface_runner",
                                     side_effect=AssertionError("must not run"), create=True):
                    status, _, body = case.post("/attended/start-surface-runner", OWNER)
                self.assertEqual(status, 403)
                self.assertIn("Nothing was started", body)
            finally:
                case.doCleanups()


class RenderAllowedEmailsTests(unittest.TestCase):
    SCRIPT = REPO / "integration" / "deployment" / "vm_pilot" / "render_allowed_emails.py"

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(self.SCRIPT), *args], capture_output=True, text=True,
                              timeout=60, cwd=tempfile.gettempdir())

    def test_renders_sorted_lowercase_deduplicated_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            users = Path(folder) / "users.txt"
            out = Path(folder) / "allowed.txt"
            users.write_text(f"# c\n{VIEWER.upper()}\n{OWNER} operator\n{VIEWER}\n{OWNER} operator\n", encoding="utf-8")
            first = self.run_script(str(users), str(out))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(out.read_bytes(), f"{OWNER}\n{VIEWER}\n".encode())
            self.assertIn("written", first.stdout)
            self.assertNotIn("pentera", first.stdout + first.stderr)  # prints no addresses
            before = out.stat().st_mtime_ns
            second = self.run_script(str(users), str(out))
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("unchanged", second.stdout)
            self.assertEqual(out.stat().st_mtime_ns, before)
            self.assertEqual(out.read_bytes(), f"{OWNER}\n{VIEWER}\n".encode())

    def test_malformed_or_empty_users_file_is_refused_and_keeps_the_old_output(self):
        with tempfile.TemporaryDirectory() as folder:
            users = Path(folder) / "users.txt"
            out = Path(folder) / "allowed.txt"
            out.write_text("previous@pentera.io\n", encoding="utf-8")
            for text in (f"{OWNER}\nnot-an-email\n", f"{OWNER} admin\n", f"{OWNER}\n{OWNER} operator\n", "# nobody\n"):
                users.write_text(text, encoding="utf-8")
                done = self.run_script(str(users), str(out))
                self.assertEqual(done.returncode, 1, text)
                self.assertTrue(done.stderr.startswith("refused:"), done.stderr)
                self.assertNotIn("pentera", done.stdout + done.stderr)
                self.assertEqual(out.read_text(encoding="utf-8"), "previous@pentera.io\n")
            self.assertEqual(self.run_script(str(Path(folder) / "missing"), str(out)).returncode, 1)
            self.assertEqual(self.run_script().returncode, 2)
            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()), ["allowed.txt", "users.txt"])

    def test_output_agrees_with_the_dashboard_allow_list(self):
        with tempfile.TemporaryDirectory() as folder:
            users = Path(folder) / "users.txt"
            out = Path(folder) / "allowed.txt"
            users.write_text(f"{OWNER} operator\n{VIEWER}\n", encoding="utf-8")
            self.assertEqual(self.run_script(str(users), str(out)).returncode, 0)
            rendered = set(out.read_text(encoding="utf-8").split())
            with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_ALLOWED_USERS": "",
                                         "SURFACE_ONBOARDING_ALLOWED_USERS_FILE": "", "SURFACE_ONBOARDING_OPERATORS": "",
                                         "SURFACE_ONBOARDING_OPERATORS_FILE": "",
                                         "SURFACE_ONBOARDING_USERS_FILE": str(users)}):
                admitted = {email for email in (OWNER, VIEWER, STRANGER) if dashboard.vm_role(email)}
            self.assertEqual(rendered, admitted)


class OriginMatrixTests(unittest.TestCase):
    problem = staticmethod(dashboard.request_origin_problem)

    def test_desktop_unchanged(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_PUBLIC_ORIGIN": PUBLIC}):
            self.assertIsNone(self.problem("GET", "127.0.0.1:8012", None, None, 8012))
            self.assertEqual(self.problem("GET", PUBLIC_HOST, None, None, 8012), "host_not_allowed")
            self.assertEqual(self.problem("POST", "127.0.0.1:8012", PUBLIC, None, 8012), "origin_not_allowed")
            self.assertIsNone(self.problem("POST", "127.0.0.1:8012", "http://127.0.0.1:8012", None, 8012))
            # Referer is not consulted on the desktop.
            self.assertIsNone(self.problem("POST", "127.0.0.1:8012", None, None, 8012, "https://evil.example/"))

    def test_vm_public_origin_matrix(self):
        with vm_env():
            self.assertIsNone(self.problem("GET", PUBLIC_HOST, None, None, 8000))
            self.assertIsNone(self.problem("GET", PUBLIC_HOST.upper(), None, None, 8000))
            self.assertIsNone(self.problem("POST", PUBLIC_HOST, PUBLIC, "same-origin", 8000, PUBLIC + "/co/CO-1"))
            self.assertIsNone(self.problem("POST", PUBLIC_HOST, None, None, 8000, PUBLIC + "/co/CO-1"))
            self.assertIsNone(self.problem("POST", PUBLIC_HOST, None, "same-origin", 8000))
            self.assertIsNone(self.problem("POST", PUBLIC_HOST, None, None, 8000))  # local script, not a web page
            refused = [
                ("GET", "127.0.0.1:8000", None, None, None, "host_not_allowed"),  # loopback Host is not the public host
                ("GET", "localhost:8000", None, None, None, "host_not_allowed"),
                ("GET", "172.26.37.20:9999", None, None, None, "host_not_allowed"),
                ("GET", "172.26.37.20", None, None, None, "host_not_allowed"),
                ("GET", "evil.example:8443", None, None, None, "host_not_allowed"),
                ("GET", None, None, None, None, "host_not_allowed"),
                ("POST", PUBLIC_HOST, "http://172.26.37.20:8443", None, None, "origin_not_allowed"),  # http scheme
                ("POST", PUBLIC_HOST, "https://172.26.37.20:9999", None, None, "origin_not_allowed"),
                ("POST", PUBLIC_HOST, "https://evil.example", "cross-site", None, "origin_not_allowed"),
                ("POST", PUBLIC_HOST, "null", None, None, "origin_not_allowed"),
                ("POST", PUBLIC_HOST, "http://127.0.0.1:8000", None, None, "origin_not_allowed"),
                ("POST", PUBLIC_HOST, PUBLIC, None, "https://evil.example/", "origin_not_allowed"),
                ("POST", PUBLIC_HOST, None, None, "https://172.26.37.20:84430/x", "origin_not_allowed"),
                ("POST", PUBLIC_HOST, None, None, "https://172.26.37.20:8443.evil.example/", "origin_not_allowed"),
                ("POST", PUBLIC_HOST, None, "cross-site", None, "cross_site_post"),
            ]
            for command, host, origin, fetch_site, referer, expected in refused:
                self.assertEqual(self.problem(command, host, origin, fetch_site, 8000, referer), expected,
                                 (command, host, origin, fetch_site, referer))

    def test_vm_ignores_extra_allowed_hosts_when_the_public_origin_is_set(self):
        with vm_env(SURFACE_ONBOARDING_ALLOWED_HOSTS="127.0.0.1:18000"):
            self.assertEqual(self.problem("GET", "127.0.0.1:18000", None, None, 8000), "host_not_allowed")

    def test_malformed_public_origin_refuses_everything(self):
        for bad in ("http://172.26.37.20:8443", "https://172.26.37.20:8443/path", "172.26.37.20:8443", "https://",
                    "https://a b"):
            with vm_env(SURFACE_ONBOARDING_PUBLIC_ORIGIN=bad):
                self.assertEqual(self.problem("GET", PUBLIC_HOST, None, None, 8000), "public_origin_invalid", bad)

    def test_vm_without_a_public_origin_keeps_the_loopback_rules(self):
        with vm_env(SURFACE_ONBOARDING_PUBLIC_ORIGIN=""):
            self.assertIsNone(self.problem("GET", "127.0.0.1:8000", None, None, 8000))
            self.assertEqual(self.problem("GET", PUBLIC_HOST, None, None, 8000), "host_not_allowed")

    def test_the_real_server_refuses_a_foreign_origin_before_any_handler(self):
        case = ServerCase()
        case.setUp()
        try:
            with vm_env(), patch.object(dashboard, "set_user_created_confirmation", side_effect=AssertionError("no")):
                status, _, _ = case.post("/attended/confirm-user-created", OWNER, extra={"Origin": "https://evil.example"}, body=b"")
                self.assertEqual(status, 403)
                status, _, _ = case.request("GET", "/", {dashboard.PROXY_IDENTITY_HEADER: OWNER}, host="evil.example")
                self.assertEqual(status, 403)
        finally:
            case.doCleanups()


class SecureCookieTests(unittest.TestCase):
    def test_secure_attribute_only_in_vm_mode(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertEqual(dashboard.cookie_attributes(), "; HttpOnly; SameSite=Strict")
            self.assertNotIn("Secure", dashboard.cookie_attributes())
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "vm"}):
            self.assertEqual(dashboard.cookie_attributes(), "; HttpOnly; SameSite=Strict; Secure")

    def test_every_set_cookie_in_the_dashboard_uses_the_shared_attributes(self):
        source = (REPO / "tools" / "serve_attended_open_onboardings_dashboard.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("HttpOnly"), 1)  # only inside cookie_attributes()
        self.assertGreaterEqual(source.count("cookie_attributes()"), 5)

    def test_cookies_set_by_the_handlers_carry_secure_in_vm_mode(self):
        class Capture(dashboard.Handler):
            def __init__(self):  # no socket: only header collection is exercised
                self.sent = []

            def send_response(self, *_):
                pass

            def send_header(self, name, value):
                self.sent.append((name, value))

            def end_headers(self):
                pass

        for runtime, expect_secure in (("desktop", False), ("vm", True)):
            with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": runtime}):
                handler = Capture()
                handler.send_redirect_with_cookies("/", [
                    dashboard.SESSION_COOKIE + "=v; Path=/" + dashboard.cookie_attributes() + "; Max-Age=1"])
            cookies = [value for name, value in handler.sent if name == "Set-Cookie"]
            self.assertEqual(len(cookies), 1)
            self.assertEqual("; Secure" in cookies[0], expect_secure)
            self.assertIn("HttpOnly", cookies[0])


class StateDirTests(unittest.TestCase):
    RUNNER_NAMES = ("RUNNER_STATE_PATH", "CHECK_STATE_PATH", "READBACK_PATH", "SCAN_STATUS_PATH", "VALIDATION_PATH",
                    "DIAGNOSTICS_PATH", "RUN_LOG_PATH", "SPYCLOUD_STATE_PATH", "RENEWAL_OUTCOMES_PATH", "MIRROR_PATH")
    DASHBOARD_NAMES = ("ATTENDED_LEONARDO_READBACK_PATH", "ATTENDED_REMINDERS_PATH", "USER_CREATED_CONFIRMATION_PATH",
                       "ID_WRITEBACK_PATH")

    def test_helper_rules(self):
        default = Path("C:/repo/integration/attended_x.json")
        clean = {"SURFACE_ONBOARDING_RUNTIME": "desktop", "SURFACE_ONBOARDING_STATE_DIR": ""}
        with patch.dict(os.environ, clean):
            self.assertIsNone(state_paths.state_dir())
            self.assertEqual(state_paths.state_file(default), default)
            self.assertEqual(state_paths.inventory_root(Path("D")), Path("D"))
        with patch.dict(os.environ, {**clean, "SURFACE_ONBOARDING_RUNTIME": "vm"}):
            # VM mode reads the published snapshot (docs/41): <state dir>/snapshots/current/<name>.
            self.assertEqual(state_paths.state_file(default), Path("/var/lib/surface-onboarding/snapshots/current/attended_x.json"))
            self.assertEqual(state_paths.inventory_root(Path("D")),
                             Path("/var/lib/surface-onboarding/snapshots/current/leonardo-inventory"))
        with patch.dict(os.environ, {**clean, "SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_STATE_DIR": "/srv/state"}):
            self.assertEqual(state_paths.state_file(default), Path("/srv/state/snapshots/current/attended_x.json"))
        with patch.dict(os.environ, {**clean, "SURFACE_ONBOARDING_STATE_DIR": "/srv/state"}):  # desktop override
            self.assertEqual(state_paths.state_file(default), Path("/srv/state/attended_x.json"))
        with patch.dict(os.environ, {**clean, "SURFACE_ONBOARDING_STATE_DIR": "relative/dir"}):
            with self.assertRaisesRegex(RuntimeError, "invalid_state_dir_configuration"):
                state_paths.state_dir()

    def test_inventory_default_root_follows_the_state_dir(self):
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_STATE_DIR": "/srv/state"}):
            self.assertEqual(leonardo_inventory.default_root(), Path("/srv/state/leonardo-inventory"))
            self.assertEqual(dashboard.inventory_root(), Path("/srv/state/leonardo-inventory"))
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_STATE_DIR": "", "SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertEqual(leonardo_inventory.default_root().parts[-2:], ("SurfaceOnboarding", "leonardo-inventory"))

    def _paths_in_subprocess(self, env_extra):
        code = ("import json, tools.attended_ce_only_playwright as r, tools.serve_attended_open_onboardings_dashboard as d;"
                "print(json.dumps({n: str(getattr(r, n)) for n in %r} | {n: str(getattr(d, n)) for n in %r}))"
                % (self.RUNNER_NAMES, self.DASHBOARD_NAMES))
        env = {key: value for key, value in os.environ.items() if not key.startswith("SURFACE_ONBOARDING_")}
        env.update(env_extra)
        done = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        return {name: Path(value) for name, value in json.loads(done.stdout.strip().splitlines()[-1]).items()}

    def test_every_module_constant_resolves_under_the_state_dir(self):
        target = Path(tempfile.gettempdir()) / "surface_state_dir_resolution_probe"
        paths = self._paths_in_subprocess({"SURFACE_ONBOARDING_STATE_DIR": str(target)})
        self.assertEqual(len(paths), len(self.RUNNER_NAMES) + len(self.DASHBOARD_NAMES))
        for name, path in paths.items():
            self.assertEqual(path.parent, target, name)
        vm_paths = self._paths_in_subprocess({"SURFACE_ONBOARDING_RUNTIME": "vm"})
        for name, path in vm_paths.items():
            self.assertEqual(path.parent.as_posix(), "/var/lib/surface-onboarding/snapshots/current", name)

    def test_desktop_constants_keep_todays_locations(self):
        paths = self._paths_in_subprocess({})
        for name, path in paths.items():
            self.assertEqual(path.parent, REPO / "integration", name)

    def test_the_test_guard_still_redirects_every_state_file(self):
        # integration/tests/__init__.py points every constant at one temp folder (the package may be imported as
        # "tests" or "integration.tests" depending on how unittest is started, so read the folder from the module).
        # (The package can be initialised twice, so each module is only compared with its own folder.)
        real = (REPO / "integration").resolve()
        for module, names in ((runner, self.RUNNER_NAMES), (dashboard, self.DASHBOARD_NAMES)):
            folder = Path(getattr(module, names[0])).resolve().parent
            self.assertTrue(folder.name.startswith("surface_test_state_"), folder)
            for name in names:
                path = getattr(module, name).resolve()
                self.assertEqual(path.parent, folder, name)
                self.assertNotEqual(path.parent, real, name)
        self.assertEqual(Path(runner.READBACK_PATH).name, Path(dashboard.ATTENDED_LEONARDO_READBACK_PATH).name)


class DeploymentArtifactTests(unittest.TestCase):
    FOLDER = REPO / "integration" / "deployment" / "vm_pilot"

    def test_review_artifacts_are_marked_and_hold_no_secrets(self):
        files = sorted(self.FOLDER.glob("*"))
        self.assertGreaterEqual(len(files), 7)
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertIn("REVIEW ARTIFACT", text, path.name)
            self.assertNotIn("BEGIN PRIVATE", text, path.name)
            self.assertNotRegex(text, r"(?i)client_secret\s*=\s*\"[^<\"]", path.name)

    def test_nginx_strips_identity_headers_and_has_no_hsts(self):
        text = (self.FOLDER / "nginx-surface-onboarding.conf.template").read_text(encoding="utf-8")
        self.assertIn("listen 172.26.37.20:8443 ssl;", text)
        self.assertIn('proxy_set_header X-Forwarded-Email        "";', text)
        self.assertNotRegex(text, r"(?m)^\s*add_header Strict-Transport-Security")
        # The secret header is always overwritten from a file-included variable; no literal value in the template.
        self.assertIn("include /etc/nginx/surface-onboarding-proxy-secret.conf;", text)
        self.assertRegex(text, r"(?m)^\s*proxy_set_header X-Surface-Proxy-Secret\s+\$surface_proxy_secret;")
        self.assertNotRegex(text, r"(?m)^\s*set \$surface_proxy_secret")

    def test_units_bind_the_expected_user_and_script(self):
        text = (self.FOLDER / "surface-onboarding-dashboard.service.template").read_text(encoding="utf-8")
        self.assertIn("User=surface-onboarding", text)
        self.assertIn("ExecStart=/opt/surface-onboarding/app/scripts/run_vm_dashboard.sh", text)
        self.assertIn("SURFACE_ONBOARDING_USERS_FILE=/etc/surface-onboarding/users.txt", text)
        self.assertIn("SURFACE_ONBOARDING_PROXY_SECRET_FILE=/etc/surface-onboarding/proxy-secret", text)
        self.assertNotRegex(text, r"(?m)^Environment=SURFACE_ONBOARDING_PROXY_SECRET=")
        self.assertNotIn("john.ostrander", text)
        for needed in ("NoNewPrivileges=yes", "ProtectSystem=strict", "MemoryMax=", "CPUQuota=", "TasksMax="):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
