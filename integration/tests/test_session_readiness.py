"""Sign in / Prepare sessions (2026-10-03): readiness rules, the action gate,
the production lock, the local unlock code, and the pinned Salesforce org.

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


class UnlockCodeTests(unittest.TestCase):
    def test_digest_match(self):
        digest = r.unlock_code_digest("ABCD-2345")
        self.assertTrue(r.unlock_code_matches("abcd-2345", digest))
        self.assertTrue(r.unlock_code_matches(" ABCD2345 ", digest))
        self.assertFalse(r.unlock_code_matches("ABCD-2346", digest))
        self.assertFalse(r.unlock_code_matches("ABCD-23456", digest))
        self.assertFalse(r.unlock_code_matches(None, digest))
        self.assertFalse(r.unlock_code_matches("ABCD-2345", ""))
        self.assertFalse(r.unlock_code_matches("ABCD-2345", digest.upper()))


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
                patch.object(dashboard.subprocess, "Popen", return_value=process) as popen:
            self.assertTrue(dashboard.start_attended_salesforce_login())
        dashboard._salesforce_login_process = None
        args = popen.call_args[0][0]
        self.assertNotIn("--set-default", args)
        self.assertEqual(args[-2:], ["--alias", "surface-onboarding"])

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
        self.assertIn("value='backoffice_production' disabled", page)
        self.assertIn("locked", page)
        self.assertIn("Not signed in", page)
        self.assertNotIn("password", page.lower())
        self.assertNotIn("token", page.lower())
        self.assertNotIn("<script", page.lower())


class UnlockGateServerTests(unittest.TestCase):
    """The real server with an unlock digest configured (as the launcher does)."""

    CODE = "ABCD-2345"

    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        env = patch.dict(os.environ, {dashboard.UNLOCK_DIGEST_ENV: r.unlock_code_digest(self.CODE)})
        env.start(); self.addCleanup(env.stop)
        dashboard._unlock_failures.clear(); self.addCleanup(dashboard._unlock_failures.clear)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def _request(self, method, path, body="", cookie=None):
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        self.addCleanup(connection.close)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        return response.status, response.getheader("Location"), response.getheader("Set-Cookie")

    def test_locked_until_the_code_is_entered(self):
        with patch.object(dashboard, "render_dashboard", side_effect=AssertionError("locked")), \
                patch.object(dashboard, "start_attended_validation_all", side_effect=AssertionError("locked")):
            self.assertEqual(self._request("GET", "/")[:2], (303, "/unlock"))
            self.assertEqual(self._request("POST", "/attended/validate-all")[0], 403)
            self.assertEqual(self._request("GET", "/unlock")[0], 200)
            self.assertEqual(self._request("POST", "/unlock", "code=WXYZ-2345")[0], 403)
        status, location, cookie = self._request("POST", "/unlock", "code=abcd-2345")
        self.assertEqual((status, location), (303, "/connection"))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        session = cookie.split(";")[0]
        with patch.object(dashboard, "load_runner_state", return_value={}):
            self.assertEqual(self._request("GET", "/connection", cookie=session)[0], 200)
        self.assertEqual(self._request("GET", "/", cookie=dashboard.UNLOCK_COOKIE + "=forged")[:2], (303, "/unlock"))

    def test_five_wrong_codes_pause_the_form(self):
        for _ in range(dashboard.UNLOCK_MAX_FAILURES):
            self.assertEqual(self._request("POST", "/unlock", "code=WXYZ-2345")[0], 403)
        self.assertEqual(self._request("POST", "/unlock", "code=" + self.CODE)[0], 403)


if __name__ == "__main__":
    unittest.main()
