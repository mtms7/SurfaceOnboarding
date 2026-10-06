"""Sign in / Prepare sessions (2026-10-03): readiness rules, the action gate,
the production lock, the Salesforce SSO login, the preflight, and the pinned
Salesforce org.

Synthetic values only; no Salesforce, Leonardo, or browser call is made.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from integration.onboarding import session_readiness as r
from integration.onboarding.session_readiness import SessionState, SessionStatus
import tools.serve_attended_open_onboardings_dashboard as dashboard

NOW = datetime(2026, 10, 3, 10, 0, 0)
ORG = "00D000000000001AAA"
READY = SessionStatus(SessionState.READY, "ok", NOW)
_login_patch = None


def setUpModule():
    # Unit tests below call handlers directly; the login gate has its own tests (LoginFlowTests).
    global _login_patch
    _login_patch = patch.object(dashboard, "login_required", return_value=False)
    _login_patch.start()


def tearDownModule():
    _login_patch.stop()


def _ready(minutes_ago: float) -> SessionStatus:
    return SessionStatus(SessionState.READY, "ok", NOW - timedelta(minutes=minutes_ago))


class ClassificationTests(unittest.TestCase):
    def test_salesforce_probe(self):
        c = r.classify_salesforce_probe
        self.assertEqual(c(0, ORG, ORG, NOW).state, SessionState.READY)
        self.assertEqual(c(0, ORG[:15], ORG, NOW).state, SessionState.READY)  # 15- vs 18-character Id
        self.assertEqual(c(0, "00D000000000002AAA", ORG, NOW).reason, "salesforce_wrong_org")
        self.assertEqual(c(0, ORG, None, NOW).reason, "salesforce_org_not_pinned")
        self.assertEqual(c(0, ORG, "not-an-id", NOW).reason, "salesforce_org_not_pinned")
        self.assertEqual(c(0, None, ORG, NOW).reason, "salesforce_schema")
        self.assertEqual(c(1, None, ORG, NOW).state, SessionState.EXPIRED)
        self.assertEqual(c(None, None, ORG, NOW, timed_out=True).reason, "salesforce_timeout")
        self.assertEqual(c(None, None, ORG, NOW, cli_missing=True).reason, "salesforce_cli_missing")

    def test_leonardo_results_and_unknown_codes_fail_closed(self):
        self.assertEqual(r.classify_leonardo_result("leonardo_session_active", NOW).state, SessionState.READY)
        self.assertEqual(r.classify_leonardo_result("leonardo_session_bootstrapped", NOW).state, SessionState.READY)
        self.assertEqual(r.classify_leonardo_result("leonardo_session_expired", NOW).state, SessionState.EXPIRED)
        self.assertEqual(r.classify_leonardo_result("development_login_timeout", NOW).state, SessionState.EXPIRED)
        for code in ("leonardo_session_unavailable", "browser_cdp_unavailable", "something_new"):
            self.assertEqual(r.classify_leonardo_result(code, NOW).state, SessionState.BLOCKED)

    def test_a_later_expired_run_demotes_a_ready_leonardo_session(self):
        later = {"CO-0001": {"result": "leonardo_session_expired", "completed_on": "2026-10-03T10:01:00"}}
        earlier = {"CO-0001": {"result": "leonardo_session_expired", "completed_on": "2026-10-03T09:59:00"}}
        other = {"CO-0001": {"result": "readback_verified", "completed_on": "2026-10-03T10:01:00"}}
        self.assertEqual(r.leonardo_expired_by_runs(READY, later).state, SessionState.EXPIRED)
        self.assertEqual(r.leonardo_expired_by_runs(READY, earlier).state, SessionState.READY)
        self.assertEqual(r.leonardo_expired_by_runs(READY, other).state, SessionState.READY)
        self.assertEqual(r.leonardo_expired_by_runs(READY, {"CO-0001": {"result": "leonardo_session_expired",
                                                                        "completed_on": "garbage"}}).state,
                         SessionState.READY)


class GateTests(unittest.TestCase):
    def test_freshness(self):
        self.assertEqual(r.effective(_ready(4), NOW, timedelta(minutes=5)).state, SessionState.READY)
        self.assertEqual(r.effective(_ready(6), NOW, timedelta(minutes=5)).state, SessionState.STALE)
        self.assertEqual(r.effective(_ready(-1), NOW, timedelta(minutes=5)).state, SessionState.STALE)  # future
        self.assertEqual(r.effective(r.NOT_SIGNED_IN, NOW, timedelta(minutes=5)).state, SessionState.NOT_SIGNED_IN)

    def test_start_needs_a_recent_leonardo_check_and_read_allows_fifteen_minutes(self):
        self.assertIsNone(r.action_gate("start", _ready(14), _ready(4), NOW))
        self.assertEqual(r.action_gate("start", _ready(14), _ready(6), NOW), "leonardo_stale")
        self.assertEqual(r.action_gate("start", _ready(16), _ready(1), NOW), "salesforce_stale")
        self.assertIsNone(r.action_gate("read", _ready(14), _ready(14), NOW))
        self.assertEqual(r.action_gate("read", r.NOT_SIGNED_IN, _ready(1), NOW), "salesforce_not_signed_in")
        self.assertEqual(r.action_gate("read", _ready(1), SessionStatus(SessionState.EXPIRED, "x", NOW), NOW),
                         "leonardo_expired")
        self.assertEqual(r.action_gate("create", READY, READY, NOW), "unknown_action")


class ProductionLockTests(unittest.TestCase):
    UTC_NOW = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    VALID = {"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1", "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "SECOPS-2026-001",
             "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED": "2026-10-01T00:00:00+00:00",
             "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": "2026-10-20T00:00:00+00:00"}

    def test_every_missing_piece_keeps_production_locked(self):
        p = r.production_unlock_problem
        self.assertEqual(p({}, self.UTC_NOW), "production_sessions_flag_missing")
        self.assertEqual(p({**self.VALID, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "x"}, self.UTC_NOW),
                         "production_approval_missing")
        self.assertEqual(p({**self.VALID, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED": "2026-10-01T00:00:00"},
                           self.UTC_NOW), "production_approval_missing")
        self.assertEqual(p({**self.VALID, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": "2026-10-02T00:00:00+00:00"},
                           self.UTC_NOW), "production_approval_expired")
        self.assertEqual(p({**self.VALID, "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": "2026-12-01T00:00:00+00:00"},
                           self.UTC_NOW), "production_approval_expired")  # longer than 31 days
        # Flag + valid approval still locked: the runner supports Development only.
        self.assertEqual(p(self.VALID, self.UTC_NOW), "production_runner_not_supported")

    def test_environment_keys(self):
        self.assertIsNone(r.environment_problem(r.LEONARDO_DEVELOPMENT, {}, self.UTC_NOW))
        self.assertEqual(r.environment_problem("", {}, self.UTC_NOW), "unknown_environment")
        self.assertEqual(r.environment_problem(r.BACKOFFICE_PRODUCTION, {}, self.UTC_NOW), "production_sessions_flag_missing")
        self.assertNotEqual(r.ENVIRONMENTS[r.LEONARDO_DEVELOPMENT].profile_dir_name,
                            r.ENVIRONMENTS[r.BACKOFFICE_PRODUCTION].profile_dir_name)


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class SalesforcePinTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(dashboard.set_session_status, "salesforce", r.NOT_SIGNED_IN)

    def test_reads_and_writes_are_pinned_to_the_alias(self):
        with patch.object(dashboard.subprocess, "run", return_value=_completed("{}")) as run:
            dashboard.sf_json(["data", "query", "--query", "SELECT 1", "--json"])
        self.assertEqual(run.call_args[0][0][-2:], ["--target-org", "surface-onboarding"])
        with patch.object(dashboard.subprocess, "run", return_value=_completed("{}")) as run:
            dashboard.sf_write_json(["data", "update", "record"])
        self.assertEqual(run.call_args[0][0][-2:], ["--target-org", "surface-onboarding"])
        with patch.dict(os.environ, {"SURFACE_SF_TARGET_ORG": "bad alias;rm"}):
            with self.assertRaises(RuntimeError):
                dashboard.salesforce_target_org()

    def test_login_never_changes_the_global_default_org(self):
        dashboard._salesforce_login_process = None
        process = Mock(); process.poll.return_value = None
        with patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                patch.object(dashboard, "salesforce_login_port_busy", return_value=False), \
                patch.object(dashboard.subprocess, "Popen", return_value=process) as popen:
            self.assertTrue(dashboard.start_attended_salesforce_login())
        dashboard._salesforce_login_process = None
        args = popen.call_args[0][0]
        self.assertNotIn("--set-default", args)
        self.assertEqual(args[-4:], ["--alias", "surface-onboarding", "--browser", "chrome"])

    def _probe(self, stdout, expected=ORG, returncode=0, side_effect=None):
        environ = {"SURFACE_SF_EXPECTED_ORG_ID": expected} if expected else {}
        with patch.dict(os.environ, environ, clear=False), \
                patch.object(dashboard.subprocess, "run", return_value=_completed(stdout, returncode),
                             side_effect=side_effect) as run:
            if not expected:
                os.environ.pop("SURFACE_SF_EXPECTED_ORG_ID", None)
            status = dashboard.probe_salesforce_readiness()
        return status, run

    def test_probe(self):
        good = json.dumps({"status": 0, "result": {"records": [{"Id": ORG}]}})
        status, run = self._probe(good)
        self.assertEqual(status.state, SessionState.READY)
        self.assertIn("--target-org", run.call_args[0][0])
        self.assertEqual(dashboard.session_statuses(include_runs=False)["salesforce"].state, SessionState.READY)
        self.assertEqual(self._probe(good, expected="00D000000000002AAA")[0].reason, "salesforce_wrong_org")
        self.assertEqual(self._probe(good, expected="")[0].reason, "salesforce_org_not_pinned")
        self.assertEqual(self._probe("{}", returncode=1)[0].state, SessionState.EXPIRED)
        two = json.dumps({"result": {"records": [{"Id": ORG}, {"Id": ORG}]}})
        self.assertEqual(self._probe(two)[0].reason, "salesforce_schema")
        timeout = subprocess.TimeoutExpired(cmd="sf", timeout=20)
        self.assertEqual(self._probe("", side_effect=timeout)[0].reason, "salesforce_timeout")


class ActionGateDashboardTests(unittest.TestCase):
    def setUp(self):
        for system in ("salesforce", "leonardo"):
            self.addCleanup(dashboard.set_session_status, system, r.NOT_SIGNED_IN)
        patcher = patch.object(dashboard, "readiness_now", return_value=NOW)
        patcher.start(); self.addCleanup(patcher.stop)

    def _set(self, salesforce, leonardo):
        dashboard.set_session_status("salesforce", salesforce)
        dashboard.set_session_status("leonardo", leonardo)

    def test_fresh_sessions_pass_without_any_probe(self):
        self._set(_ready(1), _ready(1))
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "probe_salesforce_readiness", side_effect=AssertionError("no probe")), \
                patch.object(dashboard, "check_leonardo_session", side_effect=AssertionError("no probe")):
            self.assertIsNone(dashboard.action_readiness_problem("start"))

    def test_stale_sessions_are_not_rechecked_while_a_run_is_in_progress(self):
        self._set(_ready(1), _ready(10))
        running = {"CO-0001": {"source_revision": "r1", "started_on": "2026-10-03T09:58:00"}}
        with patch.object(dashboard, "load_runner_state", return_value=running), \
                patch.object(dashboard, "check_leonardo_session", side_effect=AssertionError("no probe")):
            self.assertEqual(dashboard.action_readiness_problem("start"), "run_in_progress")

    def test_a_stale_session_is_rechecked_inline_once(self):
        self._set(_ready(1), _ready(10))
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "check_leonardo_session", return_value="leonardo_session_active") as check:
            self.assertIsNone(dashboard.action_readiness_problem("start"))
        check.assert_called_once()
        self._set(_ready(1), _ready(10))
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "check_leonardo_session", return_value="leonardo_session_expired"):
            self.assertEqual(dashboard.action_readiness_problem("start"), "leonardo_expired")

    def test_a_signing_in_session_is_not_probed(self):
        self._set(SessionStatus(SessionState.SIGNING_IN, "waiting_for_operator", NOW), _ready(1))
        with patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "probe_salesforce_readiness", side_effect=AssertionError("no probe")):
            self.assertEqual(dashboard.action_readiness_problem("read"), "session_signing_in")


class _FakeRequest:
    def __init__(self, path):
        self.path, self.pages, self.redirects = path, [], []

    def send_page(self, status, page):
        self.pages.append((int(status), page))

    def send_redirect(self, location):
        self.redirects.append(location)


def _no_launch(*_args, **_kwargs):
    raise AssertionError("nothing may launch")


class GatedRouteTests(unittest.TestCase):
    """A refused gate launches nothing, before any preflight read."""

    def _post(self, path, form, problem="leonardo_expired"):
        request = _FakeRequest(path)
        with patch.object(dashboard, "action_readiness_problem", return_value=problem), \
                patch.object(dashboard, "post_form", return_value=form), \
                patch.object(dashboard, "session_statuses",
                             return_value={"salesforce": READY, "leonardo": SessionStatus(SessionState.EXPIRED,
                                                                                         "leonardo_session_expired", NOW)}), \
                patch.object(dashboard, "evaluate_ce_only_fill_preflight", side_effect=_no_launch), \
                patch.object(dashboard, "evaluate_surface_fill_preflight", side_effect=_no_launch), \
                patch.object(dashboard, "start_attended_validation_all", side_effect=_no_launch), \
                patch.object(dashboard, "start_attended_scan_status_all", side_effect=_no_launch), \
                patch.object(dashboard, "_start_runner_mode", side_effect=_no_launch), \
                patch.object(dashboard, "record_runner_start", side_effect=_no_launch):
            dashboard.Handler.do_POST(request)
        return request

    def test_every_gated_route_refuses(self):
        form = {"reference": ["CO-0702"], "attended_create_authorized": ["1"], "scope_reviewed": ["1"],
                "source_revision": ["rev1"]}
        for path in ("/attended/validate-all", "/attended/scan-status-refresh-all", *dashboard.SESSION_GATED_ROUTES):
            with self.subTest(path=path):
                request = self._post(path, form)
                self.assertEqual(request.redirects, [])
                self.assertEqual(request.pages[0][0], 409)
                self.assertIn("Nothing was started", request.pages[0][1])
                self.assertIn("/connection", request.pages[0][1])

    def test_start_routes_need_the_strict_start_gate(self):
        self.assertEqual({k for k, v in dashboard.SESSION_GATED_ROUTES.items() if v == "start"},
                         {"/attended/start-ce-only-runner", "/attended/start-co0702-ce-only-runner",
                          "/attended/start-surface-runner"})
        self.assertTrue(set(dashboard.SESSION_GATED_ROUTES) <= dashboard.POST_ROUTES)


class PrepareSessionsRouteTests(unittest.TestCase):
    def _post(self, environment, running=None):
        request = _FakeRequest("/attended/prepare-sessions")
        calls = []
        with patch.object(dashboard, "post_form", return_value={"environment": [environment]}), \
                patch.object(dashboard, "local_browser_launch_allowed", return_value=True), \
                patch.object(dashboard, "load_runner_state", return_value=running or {}), \
                patch.object(dashboard, "prepare_sessions", side_effect=lambda: calls.append("prepare")):
            dashboard.Handler.do_POST(request)
        return request, calls

    def test_production_is_refused_before_anything_runs(self):
        request, calls = self._post(r.BACKOFFICE_PRODUCTION)
        self.assertEqual(calls, [])
        self.assertEqual(request.pages[0][0], 403)
        self.assertIn("locked", request.pages[0][1])
        request, calls = self._post("https://evil.example")
        self.assertEqual((calls, request.pages[0][0]), ([], 403))

    def test_refused_while_a_run_is_in_progress(self):
        request, calls = self._post(r.LEONARDO_DEVELOPMENT, {"CO-1": {"source_revision": "r", "started_on": "x"}})
        self.assertEqual(calls, [])
        self.assertEqual(request.pages[0][0], 409)

    def test_development_prepares_and_returns_to_the_page(self):
        request, calls = self._post(r.LEONARDO_DEVELOPMENT)
        self.assertEqual(calls, ["prepare"])
        self.assertEqual(request.redirects, ["/connection"])

    def test_prepare_opens_salesforce_sign_in_only_when_needed(self):
        def run(expired):
            status = SessionStatus(SessionState.EXPIRED if expired else SessionState.READY, "x", NOW)
            with patch.object(dashboard, "probe_salesforce_readiness", return_value=status), \
                    patch.object(dashboard, "start_attended_salesforce_login", return_value=True) as login, \
                    patch.object(dashboard, "start_leonardo_session_worker") as worker:
                dashboard.prepare_sessions()
            worker.assert_called_once_with(sign_in=True)
            return login.called
        self.addCleanup(dashboard.set_session_status, "salesforce", r.NOT_SIGNED_IN)
        self.assertTrue(run(expired=True))
        self.assertFalse(run(expired=False))


class LeonardoWorkerTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(dashboard.set_session_status, "leonardo", r.NOT_SIGNED_IN)
        patcher = patch.object(dashboard, "run_preflight", return_value={"leonardo_network": "ok"})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_no_vpn_route_blocks_without_opening_the_browser(self):
        with patch.object(dashboard, "run_preflight", return_value={"leonardo_network": "unreachable"}),                 patch.object(dashboard, "check_leonardo_session", side_effect=AssertionError("no browser")):
            dashboard._leonardo_session_work(sign_in=True)
        status = dashboard.session_statuses(include_runs=False)["leonardo"]
        self.assertEqual((status.state, status.reason), (SessionState.BLOCKED, "leonardo_unreachable"))

    def test_expired_session_waits_for_the_operator_then_is_ready(self):
        with patch.object(dashboard, "check_leonardo_session", return_value="leonardo_session_expired"),                 patch.object(dashboard, "bootstrap_leonardo_session", return_value="leonardo_session_bootstrapped"):
            dashboard._leonardo_session_work(sign_in=True)
        self.assertEqual(dashboard.session_statuses(include_runs=False)["leonardo"].state, SessionState.READY)

    def test_a_recheck_never_opens_a_sign_in(self):
        with patch.object(dashboard, "check_leonardo_session", return_value="leonardo_session_expired"),                 patch.object(dashboard, "bootstrap_leonardo_session", side_effect=AssertionError("no sign-in")):
            dashboard._leonardo_session_work(sign_in=False)
        self.assertEqual(dashboard.session_statuses(include_runs=False)["leonardo"].state, SessionState.EXPIRED)

    def test_a_crash_fails_closed_instead_of_staying_signing_in(self):
        with patch.object(dashboard, "check_leonardo_session", side_effect=RuntimeError("boom")):
            dashboard._leonardo_session_work(sign_in=True)
        status = dashboard.session_statuses(include_runs=False)["leonardo"]
        self.assertEqual((status.state, status.reason), (SessionState.BLOCKED, "leonardo_check_failed"))


class ConnectionPageTests(unittest.TestCase):
    def test_page_shows_both_sessions_and_a_locked_production_choice(self):
        with patch.object(dashboard, "load_runner_state", return_value={}):
            page = dashboard.page_salesforce_unavailable(failed=False)
        self.assertIn("Prepare sessions", page)
        self.assertIn("/attended/prepare-sessions", page)
        self.assertIn("value='leonardo_development'", page)
        self.assertNotIn("value='backoffice_production'", page)  # production cannot even be chosen
        self.assertIn("BackOffice production locked", page)
        self.assertIn("Not signed in", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("token", page.lower())
        self.assertNotIn("<script", page.lower())


class IdentityTests(unittest.TestCase):
    ALLOWED = r.allowed_operators("milton.stevenson@pentera.io, not-an-email")

    def test_allowed_operators(self):
        self.assertEqual(self.ALLOWED, frozenset({"milton.stevenson@pentera.io"}))
        self.assertEqual(r.allowed_operators(None), frozenset())

    def test_identity(self):
        good = {"email": "Milton.Stevenson@pentera.io", "preferred_username": "x@y.io", "organization_id": ORG}
        p = r.identity_problem
        self.assertIsNone(p(good, self.ALLOWED, ORG))
        self.assertIsNone(p({**good, "email": "x@y.io", "preferred_username": "milton.stevenson@pentera.io"},
                            self.ALLOWED, ORG))
        self.assertEqual(r.operator_label(good, self.ALLOWED), "milton.stevenson@pentera.io")
        self.assertEqual(p({**good, "email": "someone@pentera.io"}, self.ALLOWED, ORG), "login_operator_not_allowed")
        self.assertEqual(p({**good, "organization_id": "00D000000000002AAA"}, self.ALLOWED, ORG), "salesforce_wrong_org")
        self.assertEqual(p(good, self.ALLOWED, None), "salesforce_org_not_pinned")
        self.assertEqual(p(good, frozenset(), ORG), "login_no_allowed_operator")
        self.assertEqual(p(None, self.ALLOWED, ORG), "login_identity_unavailable")
        self.assertEqual(p({"email": "milton.stevenson@pentera.io"}, self.ALLOWED, ORG), "salesforce_wrong_org")


class _LoginProcess:
    pid = 4242

    def __init__(self):
        self.returncode, self.killed = None, False

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed, self.returncode = True, -9


class LoginFlowTests(unittest.TestCase):
    """The real server with the SSO login gate on; the Salesforce CLI is faked."""

    USERINFO = {"email": "milton.stevenson@pentera.io", "preferred_username": "milton.stevenson@pentera.io",
                "organization_id": ORG}

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        for patcher in (patch.object(dashboard, "login_required", return_value=True),
                        patch.object(dashboard, "local_browser_launch_allowed", return_value=True),
                        patch.dict(os.environ, {"SURFACE_DASHBOARD_ALLOWED_USERS": "milton.stevenson@pentera.io",
                                                "SURFACE_SF_EXPECTED_ORG_ID": ORG}),
                        patch.object(dashboard, "load_runner_state", return_value={})):
            patcher.start(); self.addCleanup(patcher.stop)
        self.process = _LoginProcess()
        self.quiet, self.workers = [], []
        self.launched = []
        self.popen = patch.object(dashboard.subprocess, "Popen",
                                  side_effect=lambda args, **k: self.launched.append(args) or self.process)
        self.port_busy = patch.object(dashboard, "salesforce_login_port_busy", return_value=False)
        self.port_busy.start(); self.addCleanup(self.port_busy.stop)
        for patcher in (self.popen,
                        patch.object(dashboard, "kill_process_tree", side_effect=lambda process: process.kill()),
                        patch.object(dashboard, "_sf_quiet", side_effect=lambda *a, **k: self.quiet.append(a) or 0),
                        patch.object(dashboard, "salesforce_userinfo", side_effect=lambda: dict(self.USERINFO)),
                        patch.object(dashboard, "start_leonardo_session_worker",
                                     side_effect=lambda sign_in: self.workers.append(sign_in) or True),
                        # Sign-out keeps the automation window: it also hosts the dashboard tab.
                        patch.object(dashboard, "close_automation_browser", side_effect=AssertionError("window stays"))):
            patcher.start(); self.addCleanup(patcher.stop)
        for cleanup in (dashboard._login_attempt.clear, dashboard._dashboard_sessions.clear):
            cleanup(); self.addCleanup(cleanup)
        for system in ("salesforce", "leonardo"):
            self.addCleanup(dashboard.set_session_status, system, r.NOT_SIGNED_IN)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _request(self, method, path, cookies=(), extra=None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        headers = {"Content-Type": "application/x-www-form-urlencoded", **(extra or {})}
        if cookies:
            headers["Cookie"] = "; ".join(cookies)
        connection.request(method, path, body="", headers=headers)
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        set_cookies = [value.split(";")[0] for name, value in response.getheaders() if name.lower() == "set-cookie"]
        self.last_referrer_policy = response.getheader("Referrer-Policy")
        return response.status, response.getheader("Location"), set_cookies, body

    def test_the_sign_in_button_works_from_a_real_browser(self):
        # 2026-10-03 live 403: under "no-referrer" Chromium sends "Origin: null" on the page's own
        # form post, which the cross-site check refuses. Pages now use "same-origin", so the
        # browser sends the dashboard's own origin, as below.
        self.assertEqual(self._request("GET", "/login")[0], 200)
        self.assertEqual(self.last_referrer_policy, "same-origin")
        origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        status, location, _, _ = self._request("POST", "/login/start",
                                               extra={"Origin": origin, "Sec-Fetch-Site": "same-origin"})
        self.assertEqual((status, location), (303, "/login"))
        self.assertEqual(self.last_referrer_policy, "same-origin")
        # The opaque origin stays refused: that is what a sandboxed cross-site frame sends.
        self.assertEqual(self._request("POST", "/login/start", extra={"Origin": "null"})[0], 403)

    def _sign_in(self):
        status, location, cookies, _ = self._request("POST", "/login/start")
        self.assertEqual((status, location), (303, "/login"))
        attempt = cookies[0]
        self.assertTrue(attempt.startswith(dashboard.ATTEMPT_COOKIE + "="))
        _status, _, _, body = self._request("GET", "/login", [attempt])
        self.assertIn("Complete the Salesforce sign-in", body)
        self.assertIn("http-equiv='refresh'", body)
        self.process.returncode = 0
        return attempt, self._request("GET", "/login", [attempt])

    def _session(self, cookies):
        return next(c for c in cookies if c.startswith(dashboard.SESSION_COOKIE + "="))

    def test_everything_needs_a_signed_in_operator(self):
        with patch.object(dashboard, "render_dashboard", side_effect=AssertionError("signed out")), \
                patch.object(dashboard, "start_attended_validation_all", side_effect=AssertionError("signed out")):
            self.assertEqual(self._request("GET", "/")[:2], (303, "/login"))
            self.assertEqual(self._request("GET", "/inventory")[:2], (303, "/login"))
            self.assertEqual(self._request("GET", "/tenants")[:2], (303, "/login"))
            self.assertEqual(self._request("POST", "/attended/validate-all")[0], 403)
            self.assertEqual(self._request("POST", "/logout")[0], 403)
            self.assertEqual(self._request("GET", "/", [dashboard.SESSION_COOKIE + "=forged"])[:2], (303, "/login"))
        status, _, _, body = self._request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn("Sign in with Salesforce", body)
        self.assertNotIn("type='password'", body)
        self.assertNotIn('type="password"', body)

    def test_sso_sign_in_then_leonardo_then_sign_out(self):
        _attempt, (status, location, cookies, _) = self._sign_in()
        self.assertEqual((status, location), (303, "/connection"))
        session = self._session(cookies)
        # A fresh SSO each time: the alias's previous CLI session was ended before the browser sign-in.
        self.assertEqual(self.quiet[0][:2], ("org", "logout"))
        self.assertEqual(self.workers, [True])
        self.assertEqual(dashboard.session_statuses(include_runs=False)["salesforce"].state, SessionState.READY)
        status, _, _, body = self._request("GET", "/connection", [session])
        self.assertEqual(status, 200)
        self.assertIn("milton.stevenson@pentera.io", body)
        self.assertIn("Sign out", body)
        status, location, _, _ = self._request("POST", "/logout", [session])
        self.assertEqual((status, location), (303, "/login"))
        self.assertEqual(self.quiet[-1][:2], ("org", "logout"))
        self.assertEqual(self._request("GET", "/", [session])[:2], (303, "/login"))

    def test_a_different_account_or_org_is_refused_and_its_cli_session_ended(self):
        cases = (({**self.USERINFO, "email": "x@pentera.io", "preferred_username": "x@pentera.io"},
                  "login_operator_not_allowed"),
                 ({**self.USERINFO, "organization_id": "00D000000000002AAA"}, "salesforce_wrong_org"))
        for userinfo, reason in cases:
            with self.subTest(reason=reason):
                self.process = _LoginProcess()
                with patch.object(dashboard, "salesforce_userinfo", return_value=userinfo):
                    _attempt, (status, _, cookies, body) = self._sign_in()
                self.assertEqual(status, 200)
                self.assertIn(reason, body)
                self.assertFalse(any(c.startswith(dashboard.SESSION_COOKIE) for c in cookies))
                self.assertEqual(self.quiet[-1][:2], ("org", "logout"))
                self.assertEqual(self.workers, [])

    def test_only_the_browser_that_started_the_sign_in_can_finish_it(self):
        self._request("POST", "/login/start")
        self.process.returncode = 0
        status, _, cookies, _ = self._request("GET", "/login")  # no attempt cookie
        self.assertEqual((status, cookies), (200, []))
        self.assertEqual(self._request("GET", "/login", [dashboard.ATTEMPT_COOKIE + "=guess"])[2], [])
        self.assertEqual(self.workers, [])

    def test_a_second_sign_in_is_refused_while_one_is_pending_and_cancel_stops_it(self):
        _status, _location, cookies, _ = self._request("POST", "/login/start")
        status, _, _, body = self._request("POST", "/login/start")
        self.assertEqual(status, 409)
        self.assertIn("login_in_progress", body)
        self._request("POST", "/login/cancel")  # another browser: ignored
        self.assertFalse(self.process.killed)
        self._request("POST", "/login/cancel", cookies)
        self.assertTrue(self.process.killed)

    def test_the_sign_in_opens_in_chrome_not_the_default_browser(self):
        self._request("POST", "/login/start")
        self.assertEqual(self.launched[-1][-2:], ["--browser", "chrome"])

    def test_a_busy_callback_port_is_reported_and_the_current_session_is_kept(self):
        # 2026-10-03: an orphaned CLI held port 1717, so each retry failed and had already logged out.
        with patch.object(dashboard, "salesforce_login_port_busy", return_value=True):
            status, _, cookies, body = self._request("POST", "/login/start")
        self.assertEqual(status, 409)
        self.assertIn("salesforce_login_port_busy", body)
        self.assertEqual((self.quiet, self.launched, cookies), ([], [], []))

    def test_a_cancelled_sso_signs_nobody_in(self):
        _status, _location, cookies, _ = self._request("POST", "/login/start")
        self.process.returncode = 1
        status, _, set_cookies, body = self._request("GET", "/login", cookies)
        self.assertIn("login_cancelled_or_failed", body)
        self.assertFalse(any(c.startswith(dashboard.SESSION_COOKIE) for c in set_cookies))

    def test_sign_out_is_refused_during_a_run(self):
        _attempt, (_s, _l, cookies, _b) = self._sign_in()
        session = self._session(cookies)
        running = {"CO-0001": {"source_revision": "r", "started_on": "2026-10-03T10:00:00"}}
        with patch.object(dashboard, "load_runner_state", return_value=running):
            status, _, _, _ = self._request("POST", "/logout", [session])
        self.assertEqual(status, 409)
        self.assertEqual(self._request("GET", "/connection", [session])[0], 200)


class ProcessTreeTests(unittest.TestCase):
    def test_cancel_stops_the_whole_cli_tree_on_windows(self):
        process = _LoginProcess()
        with patch.object(dashboard.os, "name", "nt"), \
                patch.object(dashboard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            dashboard.kill_process_tree(process)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/PID", "4242", "/T", "/F"])
        self.assertTrue(process.killed)

    def test_the_browser_setting_only_accepts_known_browsers(self):
        with patch.dict(os.environ, {"SURFACE_SF_LOGIN_BROWSER": "edge"}):
            self.assertEqual(dashboard.salesforce_login_browser(), "edge")
        with patch.dict(os.environ, {"SURFACE_SF_LOGIN_BROWSER": "evil; rm"}):
            self.assertEqual(dashboard.salesforce_login_browser(), "chrome")


class PreflightTests(unittest.TestCase):
    def test_preflight_reports_presence_and_reachability_only(self):
        with patch.object(dashboard.socket, "create_connection", side_effect=OSError("no route")), \
                patch.object(dashboard.shutil, "which", return_value=None):
            results = dashboard.run_preflight()
        self.assertEqual(results["leonardo_network"], "unreachable")
        self.assertEqual(results["salesforce_cli"], "missing")
        self.assertEqual(dashboard.preflight_snapshot()["results"], results)


if __name__ == "__main__":
    unittest.main()
