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
from integration.onboarding import leonardo_inventory, state_paths

REPO = Path(__file__).resolve().parents[2]
OWNER = "milton.stevenson@pentera.io"
VIEWER = "viewer.person@pentera.io"
STRANGER = "stranger@example.com"
PUBLIC = "https://172.26.37.20:8443"
PUBLIC_HOST = "172.26.37.20:8443"
VM_ENV = {
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

    def request(self, method, path, headers=None, body=b"", host=PUBLIC_HOST):
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
            self.assertEqual(dashboard.proxy_identity("127.0.0.1", [OWNER.upper()]), OWNER)
            self.assertEqual(dashboard.proxy_identity("::1", [OWNER]), OWNER)
            self.assertIsNone(dashboard.proxy_identity("10.1.2.3", [OWNER]))  # not the proxy
            self.assertIsNone(dashboard.proxy_identity("172.26.37.20", [OWNER]))  # own address is not loopback
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", []))
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER, VIEWER]))  # more than one value
            for bad in ("", "no-at-sign", f"{OWNER}, {VIEWER}", f"{OWNER} x", "<x>@pentera.io", "a@b", "a@@pentera.io"):
                self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [bad]), bad)
        with patch.dict(os.environ, {"SURFACE_ONBOARDING_RUNTIME": "desktop"}):
            self.assertIsNone(dashboard.proxy_identity("127.0.0.1", [OWNER]))  # desktop never trusts it

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

    def test_permitted_vm_posts_are_the_local_acknowledgements_and_read_only_checks(self):
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset({
            "/attended/mark-scan-settings-off", "/attended/mark-ce-enabled", "/attended/mark-operator-assigned",
            "/attended/confirm-user-created", "/attended/unconfirm-user-created", "/attended/duplicate-precheck",
            "/attended/rerun-ce-only-fill-preflight", "/attended/rerun-co0702-fill-preflight"}))
        self.assertTrue(dashboard.VM_OPERATOR_POSTS <= dashboard.POST_ROUTES)
        self.assertFalse(dashboard.VM_OPERATOR_POSTS & set(dashboard.SESSION_GATED_ROUTES))  # none launches the runner

    def test_operator_confirmation_is_recorded_with_the_per_user_identity(self):
        with tempfile.TemporaryDirectory() as folder, vm_env(), \
                patch.object(dashboard, "USER_CREATED_CONFIRMATION_PATH", Path(folder) / "confirmations.json"):
            status, _, _ = self.post("/attended/confirm-user-created", OWNER, "CO-0123")
            self.assertEqual(status, 303)
            record = json.loads((Path(folder) / "confirmations.json").read_text(encoding="utf-8"))["CO-0123"]
            self.assertEqual(record["confirmed_by"], OWNER)
            status, _, _ = self.post("/attended/unconfirm-user-created", OWNER, "CO-0123")
            self.assertEqual(status, 303)
            self.assertNotIn("CO-0123", json.loads((Path(folder) / "confirmations.json").read_text(encoding="utf-8")))

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
            self.assertEqual(state_paths.state_file(default), Path("/var/lib/surface-onboarding/attended_x.json"))
            self.assertEqual(state_paths.inventory_root(Path("D")), Path("/var/lib/surface-onboarding/leonardo-inventory"))
        with patch.dict(os.environ, {**clean, "SURFACE_ONBOARDING_RUNTIME": "vm", "SURFACE_ONBOARDING_STATE_DIR": "/srv/state"}):
            self.assertEqual(state_paths.state_file(default), Path("/srv/state/attended_x.json"))
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
            self.assertEqual(path.parent.as_posix(), "/var/lib/surface-onboarding", name)

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

    def test_units_bind_the_expected_user_and_script(self):
        text = (self.FOLDER / "surface-onboarding-dashboard.service.template").read_text(encoding="utf-8")
        self.assertIn("User=surface-onboarding", text)
        self.assertIn("ExecStart=/opt/surface-onboarding/app/scripts/run_vm_dashboard.sh", text)
        self.assertIn("SURFACE_ONBOARDING_ALLOWED_USERS=milton.stevenson@pentera.io", text)
        self.assertNotIn("john.ostrander", text)
        for needed in ("NoNewPrivileges=yes", "ProtectSystem=strict", "MemoryMax=", "CPUQuota=", "TasksMax="):
            self.assertIn(needed, text)


if __name__ == "__main__":
    unittest.main()
