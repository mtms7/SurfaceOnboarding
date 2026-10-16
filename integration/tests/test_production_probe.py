"""D4 read-only production probe: gate, network guard, click guard, redaction, runner wiring. All local, no browser."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from integration.onboarding import production_probe as probe
from integration.onboarding import session_readiness
from integration.onboarding.run_target import ProductionLocked, development_target
import tools.attended_ce_only_playwright as runner

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
GOOD_ENV = {probe.GATE_ENV: "1", probe.REF_ENV: "PROBE-D4-20261016"}
ORIGIN = "https://app.pentera.io"
POISON = "SECRET-VALUE-9f8e"


# --- fakes -------------------------------------------------------------------------------------------------------

class FakeRequest:
    def __init__(self, method: str, url: str) -> None:
        self.method, self.url = method, url


class FakeRoute:
    def __init__(self, method: str, url: str) -> None:
        self.request = FakeRequest(method, url)
        self.outcome = None

    def continue_(self, **_kw) -> None:
        self.outcome = "continued"

    def abort(self, *_a) -> None:
        self.outcome = "aborted"


class FakeLocator:
    def __init__(self, page: "FakePage", name: str, count: int = 1) -> None:
        self.page, self.name, self._count = page, name, count

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "FakeLocator":
        return self

    def click(self, **_kw) -> None:
        self.page.clicks.append(self.name)
        if self.name == self.page.add_label:
            self.page.form_open = True
        if self.name == "Cancel":
            self.page.form_open = False


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    def press(self, key: str) -> None:
        self.page.keys.append(key)
        if key == "Escape":
            self.page.form_open = False


class FakePage:
    def __init__(self, *, rows: int = 1, add_account: int = 1, edit: int = 1, cancel: int = 1,
                 add_label: str = "Add Account", path: str = "/backoffice/tenantManagement") -> None:
        self.url = ORIGIN + path + "?tenant=abc&token=" + POISON
        self.add_label = add_label
        self.auth_payload = {"texts": ["Add Account", "Tenants"], "tables": 1, "navs": 1}
        self.gotos: list[str] = []
        self.idle_waits = 0
        self.controls_payload = [
            {"tag": "button", "role": "", "text": "Add Account", "enabled": True},
            {"tag": "a", "role": "", "text": "owner@example.com", "enabled": True, "href": "/x?t=" + POISON},
            {"tag": "div", "role": "button", "text": "Row 123456789 " + "z" * 80, "enabled": False},
            "junk",
        ]
        self.headings_payload = ["Tenant Management", "Second", "Third", "Fourth"]
        self.clicks: list[str] = []
        self.keys: list[str] = []
        self.form_open = False
        self.keyboard = FakeKeyboard(self)
        self.rows, self.add_account, self.edit, self.cancel = rows, add_account, edit, cancel
        self.routes: list = []
        self.listeners: list = []
        self.form_payload = {
            "fields": [
                {"label": "Company name *", "name": "companyName", "type": "text", "required": True,
                 "option_count": None, "data_am": "", "value": POISON},
                {"label": "Account Type", "name": "accountType", "type": "select", "required": False,
                 "option_count": 3, "data_am": "", "value": POISON, "options": [POISON]},
                {"label": "owner@example.com", "name": "x" * 200, "type": "weird", "required": "yes"},
                "not-a-dict",
            ],
            "buttons": ["Cancel", "Confirm"],
        }

    def route(self, pattern, handler) -> None:
        self.routes.append((pattern, handler))

    def on(self, event, callback) -> None:
        self.listeners.append((event, callback))

    def title(self) -> str:
        return "Tenant Management"

    def wait_for_timeout(self, _ms) -> None:
        pass

    def bring_to_front(self) -> None:
        pass

    def wait_for_load_state(self, state, timeout=None) -> None:
        self.idle_waits += 1

    def goto(self, url, **_kw) -> None:
        self.gotos.append(url)
        self.url = url

    def evaluate(self, script):
        if script == probe.AUTH_JS:
            return self.auth_payload
        if script == probe.SETTLE_JS:
            return True
        if script == probe.VISIBLE_JS:
            return {"controls": self.controls_payload, "headings": self.headings_payload}
        return self.form_payload if self.form_open else None

    def get_by_role(self, role, name=None, exact=False) -> FakeLocator:
        counts = {("button", self.add_label): self.add_account, ("button", "Cancel"): self.cancel,
                  ("button", "Search"): 1, ("textbox", "Search"): 0, ("menuitem", "Edit"): 0,
                  ("button", "Confirm"): 1}
        return FakeLocator(self, name, counts.get((role, name), 0))

    def locator(self, selector) -> FakeLocator:
        if selector == probe.ROW_MENU_SELECTOR:
            return FakeLocator(self, "row_menu", self.rows)
        if selector == probe.EDIT_SELECTOR:
            return FakeLocator(self, "edit_item", self.edit)
        return FakeLocator(self, selector, 0)


def collect(page: FakePage):
    guard, clicks = probe.RequestGuard(), probe.ClickLog()
    guard.install(page)
    return guard, clicks, probe.collect(page, guard, clicks, dev_labels=("Company name", "Operator"),
                                        dev_names=("companyName", "mfaRequired"))


# --- gate ----------------------------------------------------------------------------------------------------------

class GateTests(unittest.TestCase):
    def test_gate_needs_flag_and_reference(self):
        self.assertEqual(probe.probe_gate(GOOD_ENV), "PROBE-D4-20261016")
        for env, code in (({}, "production_probe_flag_missing"),
                          ({probe.GATE_ENV: "true", probe.REF_ENV: "PROBE-D4-20261016"}, "production_probe_flag_missing"),
                          ({probe.GATE_ENV: "1"}, "production_probe_reference_invalid"),
                          ({probe.GATE_ENV: "1", probe.REF_ENV: "short"}, "production_probe_reference_invalid"),
                          ({probe.GATE_ENV: "1", probe.REF_ENV: "lower-case-ref"}, "production_probe_reference_invalid"),
                          ({probe.GATE_ENV: "1", probe.REF_ENV: "PROBE-D4\n"}, "production_probe_reference_invalid")):
            with self.subTest(env=env), self.assertRaises(probe.ProbeRefused) as caught:
                probe.probe_gate(env)
            self.assertEqual(caught.exception.code, code)

    def test_target_is_exact_production_origin_and_dedicated_profile(self):
        target = probe.probe_target()
        self.assertEqual(target.origin, ORIGIN)
        self.assertEqual(target.profile_dir_name, "backoffice-production-automation")
        self.assertEqual(probe.start_url(target), ORIGIN + "/backoffice/tenantManagement")
        with self.assertRaises(ValueError):
            probe.start_url(development_target())

    def test_probe_does_not_unlock_production_writes(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        env = {**GOOD_ENV, "SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1"}
        with self.assertRaises(ProductionLocked):
            runner.resolve_run_target("prod", env, NOW)


# --- network guard -------------------------------------------------------------------------------------------------

class NetworkGuardTests(unittest.TestCase):
    def test_reads_pass_and_writes_are_aborted(self):
        guard = probe.RequestGuard()
        cases = [
            ("GET", ORIGIN + "/api/v1/backoffice/tenants?x=1", "continued"),
            ("GET", "https://idp.example/login", "continued"),
            ("HEAD", ORIGIN + "/static/app.js", "continued"),
            ("POST", ORIGIN + "/api/v1/backoffice/getAllDetailedAccounts", "continued"),
            ("POST", ORIGIN + "/api/v1/backoffice/account/" + "a" * 20 + "/campaign/executions", "continued"),
            ("POST", ORIGIN + "/api/v1/backoffice/account/add", "aborted"),
            ("PUT", ORIGIN + "/api/v1/backoffice/account/" + "a" * 20 + "/edit", "aborted"),
            ("POST", ORIGIN + "/api/v1/backoffice/account/" + "a" * 20 + "/edit", "aborted"),
            ("DELETE", ORIGIN + "/api/v1/backoffice/account/" + "a" * 20, "aborted"),
            ("PATCH", ORIGIN + "/api/v1/backoffice/anything", "aborted"),
            ("POST", ORIGIN + "/api/v1/backoffice/getAllDetailedAccounts/extra", "aborted"),
            ("POST", "https://evil.example/api/v1/backoffice/getAllDetailedAccounts", "aborted"),
            ("POST", "https://app.pentera.io.evil.example/api/v1/backoffice/getAllDetailedAccounts", "aborted"),
            ("OPTIONS", ORIGIN + "/api/v1/backoffice/account/add", "aborted"),
            ("", ORIGIN + "/api/v1/x", "aborted"),
        ]
        for method, url, expected in cases:
            route = FakeRoute(method, url)
            with self.subTest(method=method, url=url):
                guard.handle(route)
                self.assertEqual(route.outcome, expected)

    def test_create_and_edit_posts_are_blocked_and_recorded_by_name_only(self):
        page = FakePage()
        guard = probe.RequestGuard()
        guard.install(page)
        handler = page.routes[0][1]
        add = FakeRoute("POST", ORIGIN + "/api/v1/backoffice/account/add?secret=" + POISON)
        edit = FakeRoute("PUT", ORIGIN + "/api/v1/backoffice/account/" + "b" * 32 + "/edit")
        read = FakeRoute("GET", ORIGIN + "/api/v1/backoffice/account/" + "b" * 32 + "/details")
        for route in (add, edit, read):
            handler(route)
        self.assertEqual((add.outcome, edit.outcome, read.outcome), ("aborted", "aborted", "continued"))
        text = json.dumps([guard.api_report(), guard.blocked_report()])
        self.assertNotIn(POISON, text)
        self.assertNotIn("b" * 32, text)
        self.assertIn("/api/v1/backoffice/account/add", text)
        self.assertIn("/api/v1/backoffice/account/{id}/edit", text)
        self.assertEqual([r["method"] for r in guard.blocked_report()], ["POST", "PUT"])

    def test_guard_aborts_when_the_request_cannot_be_read(self):
        class Broken:
            @property
            def request(self):
                raise RuntimeError("x")

            aborted = False

            def abort(self):
                self.aborted = True

        route = Broken()
        probe.RequestGuard().handle(route)
        self.assertTrue(route.aborted)

    def test_guard_install_failure_is_not_swallowed(self):
        page = mock.Mock()
        page.route.side_effect = RuntimeError("no route support")
        with self.assertRaises(RuntimeError):
            probe.RequestGuard().install(page)


# --- collection: no fill, no submit, no values -------------------------------------------------------------------------

class CollectTests(unittest.TestCase):
    def test_submit_and_other_controls_are_never_clicked(self):
        page = FakePage()
        _guard, clicks, report = collect(page)
        self.assertEqual(page.clicks, ["row_menu", "Add Account", "Cancel"])
        for forbidden in ("Confirm", "Submit", "Save", "Create", "edit_item", "Edit"):
            self.assertNotIn(forbidden, page.clicks)
        self.assertEqual(clicks.kinds, ["open_row_menu", "open_create_form", "cancel_create_form"])
        self.assertTrue(report["checks"]["submit_never_clicked"])
        self.assertTrue(report["create_form"]["submit_control_present"])
        self.assertTrue(report["create_form"]["closed"])

    def test_click_log_refuses_any_other_kind(self):
        page = FakePage()
        for kind in ("confirm", "submit", "save", "edit", "delete", ""):
            with self.subTest(kind=kind), self.assertRaises(probe.ProbeRefused):
                probe.ClickLog().click(kind, FakeLocator(page, "Confirm"))
        self.assertEqual(page.clicks, [])
        self.assertFalse(any("confirm" in k or "submit" in k for k in probe.ALLOWED_CLICKS))

    def test_missing_cancel_falls_back_to_escape_not_confirm(self):
        page = FakePage(cancel=0)
        _g, _c, report = collect(page)
        self.assertEqual(page.keys.count("Escape"), 2)  # row menu and form
        self.assertNotIn("Confirm", page.clicks)
        self.assertTrue(report["create_form"]["closed"])

    def test_form_metadata_only_and_no_values_anywhere(self):
        page = FakePage()
        _g, _c, report = collect(page)
        fields = report["create_form"]["fields"]
        self.assertEqual(len(fields), 3)
        self.assertEqual(fields[0], {"label": "Company name *", "name": "companyName", "type": "text", "required": True,
                                     "option_count": None, "data_am": ""})
        self.assertEqual(fields[2]["label"], "<redacted>")
        self.assertEqual(fields[2]["name"], "")
        self.assertEqual(fields[2]["type"], "unknown")
        self.assertFalse(fields[2]["required"])
        self.assertNotIn(POISON, json.dumps(report))
        for field in fields:
            self.assertEqual(set(field), {"label", "name", "type", "required", "option_count", "data_am"})

    def test_page_urls_are_paths_without_query(self):
        _g, _c, report = collect(FakePage())
        self.assertEqual(report["pages"][0], {"path": "/backoffice/tenantManagement", "title": "Tenant Management"})
        self.assertNotIn("tenant=abc", json.dumps(report))

    def test_search_and_edit_presence_and_dev_contract(self):
        _g, _c, report = collect(FakePage())
        self.assertEqual(report["controls"], {"search": "present", "edit": "present"})
        self.assertEqual(report["dev_contract"]["labels_missing_in_production"], ["Operator"])
        self.assertEqual(report["dev_contract"]["names_missing_in_production"], ["mfaRequired"])
        self.assertFalse(report["checks"]["dev_contract_labels_all_present"])

    def test_no_rows_means_edit_not_checked_and_no_menu_click(self):
        page = FakePage(rows=0)
        _g, _c, report = collect(page)
        self.assertEqual(report["controls"]["edit"], "not_checked_no_rows")
        self.assertNotIn("row_menu", page.clicks)
        self.assertFalse(report["checks"]["edit_control_present"])

    def test_missing_create_control_is_reported_not_forced(self):
        page = FakePage(add_account=0)
        _g, _c, report = collect(page)
        self.assertFalse(report["create_form"]["control_found"])
        self.assertNotIn("Add Account", page.clicks)
        self.assertIn("create_control_found", build(report)["checks_failed"])
        self.assertIsNone(report["create_form"]["candidate_matched"])
        self.assertTrue(report["visible_controls"])  # still finishes with the diagnostic list

    def test_candidate_labels_are_tried_and_the_match_recorded(self):
        for label in ("Create Account", "New Account", "Add account"):
            page = FakePage(add_label=label)
            _g, _c, report = collect(page)
            with self.subTest(label=label):
                self.assertEqual(report["create_form"]["candidate_matched"], label)
                self.assertEqual(page.clicks, ["row_menu", label, "Cancel"])
        self.assertNotIn("Create", probe.CREATE_CANDIDATES)
        self.assertNotIn("Add", probe.CREATE_CANDIDATES)

    def test_visible_controls_are_redacted_capped_and_headings_limited(self):
        _g, _c, report = collect(FakePage())
        controls = report["visible_controls"]
        self.assertEqual(len(controls), 3)
        self.assertEqual(set(controls[0]), {"tag", "role", "text", "enabled"})
        self.assertEqual(controls[1]["text"], "<redacted>")
        self.assertNotIn("123456789", controls[2]["text"])
        self.assertLessEqual(len(controls[2]["text"]), 40)
        self.assertFalse(controls[2]["enabled"])
        self.assertEqual(report["headings"], ["Tenant Management", "Second", "Third"])
        self.assertEqual(report["title"], "Tenant Management")
        self.assertNotIn(POISON, json.dumps(report))
        many = FakePage()
        many.controls_payload = [{"tag": "button", "role": "", "text": "b%d" % i, "enabled": True} for i in range(200)]
        self.assertEqual(len(collect(many)[2]["visible_controls"]), probe.MAX_CONTROLS)

    def test_page_settles_before_inspection_and_wrong_path_is_navigated_once_by_get(self):
        page = FakePage()
        _g, _c, report = collect(page)
        self.assertEqual(page.idle_waits, 1)
        self.assertEqual(page.gotos, [])
        self.assertTrue(report["settle"]["content_ready"])
        self.assertIsNone(report["settle"]["navigated_from"])
        page = FakePage(path="/backoffice/home")
        _g, _c, report = collect(page)
        self.assertEqual(page.gotos, [ORIGIN + "/backoffice/tenantManagement"])
        self.assertEqual(report["settle"]["navigated_from"], "/backoffice/home")
        self.assertNotIn("tenant=abc", json.dumps(report))

    def test_goto_failure_is_recorded_not_fatal(self):
        page = FakePage(path="/other")
        page.goto = mock.Mock(side_effect=RuntimeError("nav"))
        _g, _c, report = collect(page)
        self.assertEqual(page.goto.call_count, 1)
        self.assertEqual(report["settle"]["navigated_from"], "/other")
        self.assertFalse(report["checks"]["page_settled"] and report["settle"]["navigation_ok"])


def build(collected):
    return probe.build_report("PROBE-D4-20261016", NOW, "production_probe_complete", collected)


# --- report ----------------------------------------------------------------------------------------------------------

LOGIN_SNAP = {"texts": ["Log In", "Sign in with SSO", "Forgot your password?"], "tables": 0, "navs": 0}
AUTH_SNAP = {"texts": ["Add Account"], "tables": 1, "navs": 1}


def run_wait(sequence, timeout=900.0):
    """Drive wait_for_login with a scripted poll sequence on a fake clock; returns (result, polls used, messages)."""
    clock, used, said = [0.0], [0], []

    def poll():
        item = sequence[min(used[0], len(sequence) - 1)]
        used[0] += 1
        return item

    def sleeper(seconds):
        clock[0] += seconds

    result = probe.wait_for_login(poll, timeout, now=lambda: clock[0], sleeper=sleeper, say=said.append)
    return result, used[0], said


class LoginWaitTests(unittest.TestCase):
    def test_authenticated_rules(self):
        auth = probe.is_authenticated
        self.assertTrue(auth("/backoffice/tenantManagement", AUTH_SNAP))
        self.assertFalse(auth("/login", AUTH_SNAP))
        self.assertFalse(auth("/backoffice/login", AUTH_SNAP))
        self.assertFalse(auth("/backoffice/Login/sso", AUTH_SNAP))
        self.assertFalse(auth("/home", AUTH_SNAP))
        self.assertFalse(auth("/backoffice/x", LOGIN_SNAP))
        self.assertFalse(auth("/backoffice/x", {"texts": ["Log In"], "tables": 1, "navs": 1}))
        self.assertFalse(auth("/backoffice/x", {"texts": [], "tables": 0, "navs": 0}))  # no marker
        self.assertFalse(auth("/backoffice/x", None))
        self.assertFalse(auth("/backoffice/x", "junk"))
        self.assertTrue(auth("/backoffice/x", {"texts": ["Anything"], "tables": 0, "navs": 0}))
        self.assertTrue(auth("/backoffice/x", {"texts": [], "tables": 0, "navs": 1}))

    def test_login_for_n_polls_then_authenticated_completes_after_three_stable_polls(self):
        seq = [("/login", LOGIN_SNAP)] * 7 + [("/backoffice/tenantManagement", AUTH_SNAP)] * 3
        result, used, _ = run_wait(seq)
        self.assertTrue(result)
        self.assertEqual(used, 10)

    def test_two_authenticated_polls_are_not_enough(self):
        seq = [("/login", LOGIN_SNAP)] * 3 + [("/backoffice/t", AUTH_SNAP)] * 2 + [("/login", LOGIN_SNAP)]
        self.assertFalse(run_wait(seq, timeout=30.0)[0])

    def test_flicker_of_one_authenticated_poll_does_not_complete(self):
        seq = ([("/login", LOGIN_SNAP)] * 2 + [("/backoffice/t", AUTH_SNAP)] + [("/login", LOGIN_SNAP)]) * 20
        self.assertFalse(run_wait(seq, timeout=60.0)[0])

    def test_never_authenticated_times_out_and_reminds_each_minute(self):
        result, used, said = run_wait([("/login", LOGIN_SNAP)], timeout=900.0)
        self.assertFalse(result)
        self.assertGreaterEqual(used, 890)
        self.assertEqual(said.count("waiting for sign-in"), 14)

    def test_poll_errors_count_as_not_signed_in(self):
        def poll():
            raise RuntimeError("cdp")
        clock = [0.0]
        self.assertFalse(probe.wait_for_login(poll, 10.0, now=lambda: clock[0],
                                              sleeper=lambda s: clock.__setitem__(0, clock[0] + s), say=lambda m: None))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="probe_test_"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_report_is_written_under_stamped_name_and_summary_is_one_line(self):
        report = build(collect(FakePage())[2])
        path = probe.write_report(self.base, report, NOW)
        self.assertEqual(path, self.base / "production-probe" / "20261016T120000Z.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["production_write"], "not_performed")
        line = probe.summary_line(report, path)
        self.assertNotIn("\n", line)
        self.assertEqual(json.loads(line)["report"], "20261016T120000Z.json")

    def test_unredacted_report_is_refused(self):
        for bad in ("owner@example.com", "a" * 32, "eyJhbGciOiJIUzI1NiJ9", "Bearer abc", "password: x"):
            report = {**build(None), "note": bad}
            with self.subTest(bad=bad), self.assertRaises(probe.ProbeRefused):
                probe.write_report(self.base, report, NOW)
        self.assertFalse((self.base / "production-probe").exists())


# --- runner wiring ------------------------------------------------------------------------------------------------------

class RunnerWiringTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="probe_runner_test_"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    def test_dev_target_is_the_default_and_unchanged(self):
        self.assertEqual(runner.DEV_TARGET, development_target())
        self.assertEqual(runner.DEVELOPMENT_ORIGIN, "https://leonardo.dev.app.pentera.io")
        self.assertEqual(runner.TENANT_MANAGEMENT, "https://leonardo.dev.app.pentera.io/backoffice/tenantManagement")
        self.assertEqual(runner.DEVELOPMENT_LOGIN, "https://leonardo.dev.app.pentera.io/login")
        self.assertEqual(runner.resolve_run_target("dev"), development_target())
        with mock.patch.dict("os.environ", {"SURFACE_LEONARDO_PROFILE_DIR": str(self.base / "dev")}):
            self.assertEqual(runner.leonardo_profile(), (self.base / "dev", True))

    def test_production_write_target_cannot_be_built(self):
        good = {"SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1", "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF": "APPROVAL-2026-10-16",
                "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_ISSUED": (NOW - timedelta(days=1)).isoformat(),
                "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": (NOW + timedelta(days=5)).isoformat()}
        with self.assertRaises(ProductionLocked) as caught:
            runner.resolve_run_target("prod", good, NOW)
        self.assertEqual(caught.exception.reason, "production_runner_not_supported")
        with self.assertRaises(ProductionLocked):
            runner.resolve_run_target("prod", {}, NOW)
        with self.assertRaises(ValueError):
            runner.resolve_run_target("staging")
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    def test_probe_profile_is_separate_from_dev_and_ignores_dev_override(self):
        with mock.patch.dict("os.environ", {"SURFACE_LEONARDO_PROFILE_DIR": str(self.base / "dev"),
                                            "LOCALAPPDATA": str(self.base)}), mock.patch.object(runner.os, "name", "nt"):
            profile, persist = runner.leonardo_profile(probe.probe_target())
        self.assertTrue(persist)
        self.assertEqual(profile.name, "backoffice-production-automation")
        self.assertNotEqual(profile, self.base / "dev")

    def test_cdp_open_tab_accepts_only_the_targets_origin(self):
        with mock.patch.object(runner, "_cdp_json", return_value={"id": "ABC-123"}) as cdp:
            self.assertIsNone(runner._cdp_open_tab(1, ORIGIN + "/backoffice/tenantManagement"))
            cdp.assert_not_called()
            self.assertEqual(runner._cdp_open_tab(1, ORIGIN + "/backoffice/tenantManagement", ORIGIN), "ABC-123")
            self.assertIsNone(runner._cdp_open_tab(1, "https://app.pentera.io.evil.example/x", ORIGIN))

    def test_probe_refused_without_gate_never_touches_playwright(self):
        with mock.patch.object(runner, "_open_automation_tab") as opened:
            for env in ({}, {probe.GATE_ENV: "1"}, {probe.GATE_ENV: "0", probe.REF_ENV: "PROBE-D4-20261016"}):
                line = json.loads(runner.run_production_probe(env, NOW, report_base=self.base))
                self.assertTrue(line["result"].startswith("production_probe_"))
                self.assertEqual(line["production_write"], "not_performed")
            opened.assert_not_called()
        self.assertFalse((self.base / "production-probe").exists())

    def test_probe_signed_in_requires_exact_origin_and_non_login_path(self):
        self.assertTrue(runner._probe_signed_in(ORIGIN + "/backoffice/tenantManagement?x=1"))
        for url in (ORIGIN + "/login", ORIGIN + "/sso/start", "https://app.pentera.io.evil.example/backoffice",
                    "https://leonardo.dev.app.pentera.io/backoffice/tenantManagement", "https://idp.example/", "about:blank"):
            with self.subTest(url=url):
                self.assertFalse(runner._probe_signed_in(url))

    def test_full_probe_run_with_fake_browser_writes_redacted_report(self):
        page = FakePage()
        browser = mock.Mock()
        tab = mock.Mock(persist=False, profile_dir=self.base)
        fake_module = mock.MagicMock()
        fake_module.sync_playwright.return_value.__enter__.return_value = mock.Mock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(), "playwright.sync_api": fake_module}), \
                mock.patch.object(runner, "_open_automation_tab", return_value=tab) as opened, \
                mock.patch.object(runner, "_probe_wait_for_login", return_value=True), \
                mock.patch.object(runner, "_probe_attach", return_value=(browser, page)), \
                mock.patch.object(runner, "_release_automation_tab") as released:
            line = json.loads(runner.run_production_probe(GOOD_ENV, NOW, report_base=self.base))
        self.assertEqual(opened.call_args.args[1], ORIGIN + "/backoffice/tenantManagement")
        self.assertEqual(opened.call_args.args[2].profile_dir_name, "backoffice-production-automation")
        released.assert_called_once()
        self.assertEqual(line["result"], "production_probe_complete")
        self.assertEqual(line["production_write"], "not_performed")
        self.assertNotIn("Confirm", page.clicks)
        saved = (self.base / "production-probe" / "20261016T120000Z.json").read_text(encoding="utf-8")
        self.assertNotIn(POISON, saved)
        self.assertEqual(page.routes[0][0], "**/*")

    def test_page_still_on_login_never_reports_complete_and_is_not_inspected(self):
        on_login_path = FakePage(path="/login")
        on_login_page = FakePage()
        on_login_page.auth_payload = LOGIN_SNAP
        for fake in (on_login_path, on_login_page):
            fake.goto = lambda *a, **k: None  # the site keeps showing the login page
        for page in (on_login_path, on_login_page):
            browser = mock.Mock()
            tab = mock.Mock(persist=False, profile_dir=self.base)
            fake_module = mock.MagicMock()
            with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(), "playwright.sync_api": fake_module}), \
                    mock.patch.object(runner, "_open_automation_tab", return_value=tab), \
                    mock.patch.object(runner, "_probe_wait_for_login", return_value=True), \
                    mock.patch.object(runner, "_probe_attach", return_value=(browser, page)), \
                    mock.patch.object(runner, "_release_automation_tab"):
                line = json.loads(runner.run_production_probe(GOOD_ENV, NOW, report_base=self.base))
            self.assertEqual(line["result"], "production_probe_login_timeout")
            self.assertEqual(page.clicks, [])
            self.assertEqual(page.routes, [])  # not even guarded: nothing was inspected

    def test_visible_page_is_navigated_fronted_and_inspected_only_after_three_authenticated_polls(self):
        page = FakePage(path="/login")
        page.url = "about:blank"
        events: list[str] = []
        auth_results = [LOGIN_SNAP] * 4 + [AUTH_SNAP]  # then AUTH_SNAP forever
        polls = {"n": 0}
        original_eval = page.evaluate

        def evaluate(script):
            if script == probe.AUTH_JS:
                snap = auth_results[min(polls["n"], len(auth_results) - 1)]
                polls["n"] += 1
                events.append("auth:" + ("yes" if snap is AUTH_SNAP else "no"))
                if snap is AUTH_SNAP:
                    page.url = ORIGIN + "/backoffice/tenantManagement"
                return snap
            events.append("inspect")
            return original_eval(script)

        def goto(url, **_kw):
            events.append("goto:" + url)
            page.url = ORIGIN + "/login"

        page.evaluate, page.goto = evaluate, goto
        page.bring_to_front = lambda: events.append("front")
        original_route = page.route
        page.route = lambda *a: (events.append("route"), original_route(*a))
        tab = mock.Mock(persist=False, profile_dir=self.base)
        fake_module = mock.MagicMock()
        said: list[str] = []
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(), "playwright.sync_api": fake_module}), \
                mock.patch.object(runner, "_open_automation_tab", return_value=tab), \
                mock.patch.object(runner, "_probe_attach", return_value=(mock.Mock(), page)), \
                mock.patch.object(runner, "_release_automation_tab"), \
                mock.patch("time.sleep"), \
                mock.patch("builtins.print", side_effect=lambda *a, **k: said.append(" ".join(map(str, a)))):
            line = json.loads(runner.run_production_probe(GOOD_ENV, NOW, report_base=self.base))
        self.assertEqual(line["result"], "production_probe_complete")
        self.assertEqual(events[0], "goto:" + ORIGIN + "/backoffice/tenantManagement")
        self.assertEqual(events[1], "front")
        first_inspection = min(i for i, e in enumerate(events) if e in ("route", "inspect"))
        authenticated_before = [e for e in events[:first_inspection] if e == "auth:yes"]
        self.assertGreaterEqual(len(authenticated_before), 3)
        self.assertNotIn("auth:no", events[first_inspection:first_inspection + 1])
        self.assertTrue(page.routes)  # the same page object was guarded and inspected
        self.assertEqual(page.clicks, ["row_menu", "Add Account", "Cancel"])
        self.assertIn("Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes", said)

    def test_login_timeout_writes_a_failed_report_without_collecting(self):
        page = FakePage(path="/login")
        tab = mock.Mock(persist=False, profile_dir=self.base)
        fake_module = mock.MagicMock()
        with mock.patch.dict("sys.modules", {"playwright": mock.MagicMock(), "playwright.sync_api": fake_module}), \
                mock.patch.object(runner, "_open_automation_tab", return_value=tab), \
                mock.patch.object(runner, "_probe_wait_for_login", return_value=False), \
                mock.patch.object(runner, "_probe_attach", return_value=(mock.Mock(), page)), \
                mock.patch.object(runner, "_release_automation_tab"):
            line = json.loads(runner.run_production_probe(GOOD_ENV, NOW, report_base=self.base))
        self.assertEqual(line["result"], "production_probe_login_timeout")
        self.assertEqual((page.routes, page.clicks), ([], []))

    def test_main_flag_is_exclusive_and_dispatches(self):
        with mock.patch.object(runner, "run_production_probe", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["runner", "--production-probe"]):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once()
        with mock.patch.object(runner.sys, "argv", ["runner", "--production-probe", "--co", "CO-0797"]), \
                self.assertRaises(SystemExit):
            runner.main()

    def test_probe_code_has_no_create_path(self):
        source = Path(probe.__file__).read_text(encoding="utf-8")
        for needle in ("Confirm\", exact", "account/add\"", ".fill(", ".type(", ".check(", ".select_option(", ".press_sequentially("):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main()
