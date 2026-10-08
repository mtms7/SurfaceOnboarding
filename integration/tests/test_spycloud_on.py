"""Manual SpyCloud ON tool (turns SpyCloud back ON on tenants turned OFF under the pre-2026-10-08 rule).

Mirrors test_spycloud_off.py: no network, no browser, recording fakes and temporary state files only.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.tests.test_spycloud_off import (
    FakePage, HAZARDS, ID, OTHER_ID, UUID, _Response, edit_url, rows, search_results,
)


def run_on(page, *, confirm_write, results, **kw):
    with search_results(*results):
        return runner.set_spycloud_on(page, "Tango - CE Only", kw.pop("expected_id", ID), expected_uuid=UUID,
                                      confirm_write=confirm_write, **kw)


class SetSpycloudOnTests(unittest.TestCase):
    def assertSafe(self, page):
        for selector in page.selectors:
            for hazard in HAZARDS:
                self.assertNotIn(hazard, selector)
        self.assertEqual([s for s in page.selectors if "data-am" in s], [runner.SPYCLOUD_EDIT_SELECTOR])
        self.assertEqual({s for s in page.selectors if "input" in s}, {runner.SPYCLOUD_CHECKBOX_SELECTOR})
        self.assertLessEqual(page.clicked.count("checkbox"), 1)

    def test_dry_run_reports_off_and_cancels_without_saving(self):
        page = FakePage(checked=False, response=None)
        self.assertEqual(run_on(page, confirm_write=False, results=[rows(enabled=False)]), "spycloud_dry_run_off")
        self.assertNotIn("checkbox", page.clicked)
        self.assertNotIn("Confirm", page.clicked)
        self.assertEqual(page.clicked[-1], "Cancel")
        self.assertFalse(page.checked)
        self.assertSafe(page)

    def test_already_on_never_saves_even_with_confirm_write(self):
        for confirm_write in (False, True):
            page = FakePage(checked=True, response=None)
            self.assertEqual(run_on(page, confirm_write=confirm_write, results=[rows(enabled=True)]),
                             "spycloud_already_on")
            self.assertEqual(page.clicked[-1], "Cancel")
            self.assertNotIn("Confirm", page.clicked)
            self.assertNotIn("checkbox", page.clicked)
            self.assertSafe(page)

    def test_confirmed_save_ticks_only_spycloud_confirms_once_and_verifies_the_row(self):
        page = FakePage(checked=False)
        outcome = run_on(page, confirm_write=True, results=[rows(enabled=False), rows(enabled=True)])
        self.assertEqual(outcome, "spycloud_on_verified")
        self.assertTrue(page.checked)
        self.assertEqual(page.clicked, ["tr, [role='row']", runner.SCAN_EXEC_ROW_MENU_SELECTOR,
                                        runner.SPYCLOUD_EDIT_SELECTOR, "expander", "expander", "checkbox", "Confirm"])
        self.assertEqual(page.clicked.count("Confirm"), 1)  # exactly one /edit save
        self.assertEqual(page.keys, [])
        self.assertSafe(page)
        self.assertEqual(runner.spycloud_write_label(outcome, True), "verified")

    def test_save_failures_mirror_the_off_path(self):
        page = FakePage(checked=False, response=_Response(edit_url(OTHER_ID)))
        self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False)]), "spycloud_save_id_mismatch")
        for status in (400, 500):
            page = FakePage(checked=False, response=_Response(edit_url(), status))
            self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False)]), "spycloud_save_failed")
        for status in (401, 403):
            page = FakePage(checked=False, response=_Response(edit_url(), status))
            with self.assertRaises(runner.LeonardoSessionExpired):
                run_on(page, confirm_write=True, results=[rows(enabled=False)])
        page = FakePage(checked=False, response=None)
        self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False)]), "spycloud_save_no_signal")
        self.assertLessEqual(page.clicked.count("Confirm"), 1)
        self.assertEqual(page.clicked[-1], "Cancel")

    def test_readback_still_off_missing_and_saved_unverified(self):
        for second, code in ((rows(enabled=False), "spycloud_readback_still_off"),
                             (rows(enabled="yes"), "spycloud_readback_missing")):
            page = FakePage(checked=False)
            self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False), second]), code)
            self.assertEqual(runner.spycloud_write_label(code, True), "attempted_unverified")
            self.assertEqual(runner.spycloud_write_label(code, False), "not_performed")
        for failed in (None, runner.TenantSearchResult([], 0)):
            page = FakePage(checked=False)
            code = run_on(page, confirm_write=True, results=[rows(enabled=False), failed, failed])
            self.assertEqual(code, "spycloud_saved_unverified")
            self.assertEqual(runner.spycloud_write_label(code, True), "attempted_unverified")
        page = FakePage(checked=False)
        self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False), None, rows(enabled=True)]),
                         "spycloud_on_verified")

    def test_reason_codes_before_any_save_cancel_the_form(self):
        cases = (
            (dict(expanders=0, checkboxes=0), "spycloud_advanced_options_unavailable"),
            (dict(expanders=2, checkboxes=0), "spycloud_checkbox_missing"),
            (dict(checkboxes=2), "spycloud_checkbox_ambiguous"),
            (dict(checked_error=True), "spycloud_checkbox_unreadable"),
            (dict(toggle_works=False), "spycloud_check_failed"),
            (dict(confirm=0), "spycloud_confirm_unavailable"),
            (dict(confirm=2), "spycloud_confirm_unavailable"),
        )
        for options, code in cases:
            page = FakePage(**{"checked": False, **options})
            with self.subTest(code=code):
                self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False)]), code)
                self.assertEqual(page.clicked[-1], "Cancel")
                self.assertNotIn("Confirm", page.clicked)
                self.assertSafe(page)

    def test_edit_unavailable_cancel_unavailable_and_tenant_proof(self):
        page = FakePage(checked=False, click_fails=(runner.SPYCLOUD_EDIT_SELECTOR,))
        self.assertEqual(run_on(page, confirm_write=True, results=[rows(enabled=False)]), "spycloud_edit_unavailable")
        for checked, found_rows in ((True, rows(enabled=True)), (False, rows(enabled=False))):
            page = FakePage(checked=checked, cancel=0)
            self.assertEqual(run_on(page, confirm_write=False, results=[found_rows]), "spycloud_cancel_unavailable")
        for results, code in (([runner.TenantSearchResult([], 0)], "spycloud_tenant_not_found"),
                              ([None], "spycloud_readback_unavailable"),
                              ([rows(enabled=False, extra=1)], "spycloud_row_ambiguous")):
            page = FakePage(checked=False)
            self.assertEqual(run_on(page, confirm_write=True, results=results), code)
            self.assertEqual(page.clicked, [])

    def test_production_origin_or_malformed_id_is_refused_before_any_page_action(self):
        for page, tenant_id in ((FakePage(url="https://leonardo.app.pentera.io/backoffice/tenantManagement"), ID),
                                (FakePage(), "bad id!")):
            with patch.object(runner, "_search_tenants", side_effect=AssertionError("no search")):
                self.assertEqual(runner.set_spycloud_on(page, "Tango", tenant_id, confirm_write=True),
                                 "spycloud_environment_not_supported")
            self.assertEqual((page.selectors, page.clicked), ([], []))

    def test_off_path_is_unchanged(self):
        page = FakePage(checked=True)
        with search_results(rows(enabled=True), rows(enabled=False)):
            self.assertEqual(runner.set_spycloud_off(page, "Tango - CE Only", ID, expected_uuid=UUID,
                                                     confirm_write=True), "spycloud_off_verified")
        page = FakePage(checked=True)
        with search_results(rows(enabled=True), rows(enabled=True)):
            self.assertEqual(runner.set_spycloud_off(page, "Tango - CE Only", ID, expected_uuid=UUID,
                                                     confirm_write=True), "spycloud_readback_still_on")
        self.assertEqual(runner.ROW_ACTIONS_ALLOWED, frozenset({runner.SCAN_EXEC_DETAILS_SELECTOR,
                                                                runner.SPYCLOUD_EDIT_SELECTOR}))


class OutcomeSemanticsTests(unittest.TestCase):
    def test_states_and_ok_outcomes(self):
        self.assertEqual([runner.spycloud_state_of(o) for o in ("spycloud_on_verified", "spycloud_already_on",
                                                              "spycloud_dry_run_off", "spycloud_readback_still_off")],
                         ["on", "on", "off", "unknown"])
        for outcome in ("spycloud_on_verified", "spycloud_already_on", "spycloud_dry_run_off"):
            self.assertIn(outcome, runner.SPYCLOUD_OK_OUTCOMES)
        self.assertNotIn("spycloud_readback_still_off", runner.SPYCLOUD_OK_OUTCOMES)
        self.assertEqual(runner.spycloud_write_label("spycloud_already_on", True), "not_performed")
        self.assertEqual(runner.spycloud_write_label("spycloud_dry_run_off", True), "not_performed")

    def test_record_modes_and_warning_flags(self):
        path = Path(tempfile.mkdtemp()) / "attended_spycloud.json"
        log = runner.RunLog("CO-0679", "spycloud_on")
        with patch.object(runner, "SPYCLOUD_STATE_PATH", path), patch.object(runner, "_log", return_value=log):
            runner._record_spycloud("CO-0679", "spycloud_on_verified", "standalone")
            runner._record_spycloud("CO-0757", "spycloud_dry_run_off", "dry_run")
            runner._record_spycloud("CO-0728", "spycloud_saved_unverified", "standalone")
            runner._record_spycloud("CO-0767", "spycloud_readback_still_off", "standalone")
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual({k: (v["outcome"], v["mode"], v["warning"]) for k, v in state.items()},
                         {"CO-0679": ("spycloud_on_verified", "standalone", False),
                          "CO-0757": ("spycloud_dry_run_off", "dry_run", False),
                          "CO-0728": ("spycloud_saved_unverified", "standalone", True),
                          "CO-0767": ("spycloud_readback_still_off", "standalone", True)})
        steps = [(e["step"], e["outcome"]) for e in log.events]
        self.assertEqual(steps.count(("spycloud_warning", "not_verified")), 2)
        self.assertEqual([s for s, _ in steps if s == "spycloud_note"], ["spycloud_note"])  # only the OFF dry run


class StandaloneOnRunTests(unittest.TestCase):
    def setUp(self):
        import types
        directory = Path(tempfile.mkdtemp())
        self.state = directory / "attended_spycloud.json"
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: contextlib.nullcontext(object())
        package = types.ModuleType("playwright")
        package.sync_api = fake

        @contextlib.contextmanager
        def attended(_playwright):
            yield types.SimpleNamespace(on=lambda *a, **k: None, url=runner.DEVELOPMENT_ORIGIN + "/backoffice/tenantManagement")
        stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": fake}))
        for name, value in (("SPYCLOUD_STATE_PATH", self.state), ("RUN_LOG_PATH", directory / "log.json")):
            stack.enter_context(patch.object(runner, name, value))
        stack.enter_context(patch.object(runner, "_attended_page", attended))
        stack.enter_context(patch.object(runner, "_readback_ids", return_value=(ID, UUID)))

    def test_default_is_a_dry_run_and_the_confirm_flag_is_passed_through(self):
        for kwargs, flag, mode in (({}, False, "dry_run"), ({"confirm_write": True}, True, "standalone")):
            with patch.object(runner, "set_spycloud_on", return_value="spycloud_dry_run_off") as call, \
                    patch.object(runner, "set_spycloud_off", side_effect=AssertionError("wrong direction")), \
                    patch.object(runner, "_validation_route", return_value=(runner.CE_ENGINE, "Tango - CE Only")):
                self.assertEqual(runner.run_spycloud_on("CO-0757", **kwargs), "spycloud_dry_run_off")
            self.assertIs(call.call_args.kwargs["confirm_write"], flag)
            self.assertEqual(call.call_args.args[1:3], ("Tango - CE Only", ID))
            self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["CO-0757"]["mode"], mode)

    def test_production_not_onboarded_route_and_session_expiry(self):
        with patch.object(runner, "set_spycloud_on", side_effect=AssertionError("untouched")):
            with patch.object(runner, "_readback_ids", side_effect=AssertionError("untouched")):
                for env in ("prod", "prod-clone", "", "DEV"):
                    self.assertEqual(runner.run_spycloud_on("CO-0757", confirm_write=True, env_name=env),
                                     "spycloud_environment_not_supported")
            with patch.object(runner, "_readback_ids", return_value=None):
                self.assertEqual(runner.run_spycloud_on("CO-0757", confirm_write=True), "spycloud_not_onboarded")
            with patch.object(runner, "_validation_route", return_value=(runner.SURFACE_ENGINE, "Tango")):
                self.assertEqual(runner.run_spycloud_on("CO-0757", confirm_write=True), "spycloud_route_not_applicable")
            self.assertEqual(runner.run_spycloud_on("not-a-co"), "invalid_co_reference")
        self.assertFalse(self.state.exists())
        with patch.object(runner, "set_spycloud_on", side_effect=runner.LeonardoSessionExpired()), \
                patch.object(runner, "_validation_route", return_value=(runner.CE_ENGINE, "Tango - CE Only")):
            self.assertEqual(runner.run_spycloud_on("CO-0757", confirm_write=True), "leonardo_session_expired")
        self.assertFalse(self.state.exists())

    def test_a_verified_ce_renewal_mirror_is_eligible(self):
        unsupported = runner.SurfaceSourceError("validation_route_unsupported")
        record = {"CO-0767": {"mirror_of_production": True, "status": "verified"}}
        sf_row = [{"Name": "CO-0767", "Account_Name__c": "A2A", "Onboarding_Product__c": "Surface & Credential Exposure",
                   "Onboarding_Type__c": "Renewal of Existing Product"}]
        with patch.object(runner, "_validation_route", side_effect=unsupported), \
                patch.object(runner, "_mirror_records", return_value=record), \
                patch.object(runner, "_sf_records", return_value=sf_row), \
                patch.object(runner, "set_spycloud_on", return_value="spycloud_dry_run_off") as call:
            self.assertEqual(runner.run_spycloud_on("CO-0767"), "spycloud_dry_run_off")
        self.assertEqual(call.call_args.args[1], "A2A")
        surface_only = [{**sf_row[0], "Onboarding_Product__c": "Surface"}]
        with patch.object(runner, "_validation_route", side_effect=unsupported), \
                patch.object(runner, "_mirror_records", return_value=record), \
                patch.object(runner, "_sf_records", return_value=surface_only), \
                patch.object(runner, "set_spycloud_on", side_effect=AssertionError("untouched")):
            self.assertEqual(runner.run_spycloud_on("CO-0767"), "validation_route_unsupported")


class CommandLineOnTests(unittest.TestCase):
    def main(self, *argv):
        with patch.object(sys, "argv", ["runner", *argv]), patch("builtins.print") as printed:
            code = runner.main()
        return code, json.loads(printed.call_args.args[0])

    def test_dry_run_default_confirm_explicit_and_prod_passed_for_refusal(self):
        with patch.object(runner, "run_spycloud_on", return_value="spycloud_dry_run_off") as on, \
                patch.object(runner, "run_spycloud_off", side_effect=AssertionError("wrong direction")):
            _code, report = self.main("--co", "CO-0679", "--spycloud-on")
            self.assertEqual(on.call_args.kwargs, {"confirm_write": False, "env_name": "dev"})
            self.assertEqual(report["leonardo_write"], "not_performed")
            self.main("--co", "CO-0679", "--spycloud-on", "--confirm-write")
            self.assertEqual(on.call_args.kwargs, {"confirm_write": True, "env_name": "dev"})
            self.main("--co", "CO-0679", "--spycloud-on", "--confirm-write", "--env", "prod")
            self.assertEqual(on.call_args.kwargs["env_name"], "prod")
        with patch.object(runner, "run_spycloud_on", return_value="spycloud_on_verified"):
            _code, report = self.main("--co", "CO-0679", "--spycloud-on", "--confirm-write")
            self.assertEqual(report["leonardo_write"], "verified")
            self.assertEqual(report["salesforce_writeback"], "not_performed")

    def test_off_flag_still_calls_the_off_run(self):
        with patch.object(runner, "run_spycloud_off", return_value="spycloud_dry_run_on") as off, \
                patch.object(runner, "run_spycloud_on", side_effect=AssertionError("wrong direction")):
            self.main("--co", "CO-0757", "--spycloud-off")
            off.assert_called_once()

    def test_usage_errors_and_mutual_exclusion(self):
        for argv in (["--spycloud-on"], ["--co", "CO-0679", "--confirm-write"],
                     ["--co", "CO-0679", "--spycloud-on", "--spycloud-off"],
                     ["--co", "CO-0679", "--spycloud-on", "--renew"],
                     ["--co", "CO-0679", "--spycloud-on", "--mirror-renewal"],
                     ["--co", "CO-0679", "--spycloud-on", "--renew-run", "--revision", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()), \
                    patch.object(runner, "run_spycloud_on", side_effect=AssertionError("no run")), \
                    patch.object(runner, "run_spycloud_off", side_effect=AssertionError("no run")):
                self.main(*argv)

    def test_real_prod_is_refused_before_any_work(self):
        with patch.object(runner, "_readback_ids", side_effect=AssertionError("untouched")):
            _code, report = self.main("--co", "CO-0679", "--spycloud-on", "--confirm-write", "--env", "prod")
        self.assertEqual(report["result"], "spycloud_environment_not_supported")
        self.assertEqual(report["leonardo_write"], "not_performed")


class DashboardOnTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "attended_spycloud.json"
        patcher = patch.object(dashboard, "SPYCLOUD_STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def card(self, outcome, mode="standalone"):
        self.path.write_text(json.dumps({"CO-0679": {"outcome": outcome, "mode": mode,
                                                       "observed_at": "2026-10-08T12:00:00", "warning": False}}),
                             encoding="utf-8")
        return dashboard._spycloud_section("CO-0679")

    def test_on_outcomes_render_green_and_off_dry_run_amber(self):
        for outcome in ("spycloud_on_verified", "spycloud_already_on"):
            card = self.card(outcome)
            self.assertIn("SpyCloud is ON (default)", card)
            self.assertIn("source-ready", card)
            self.assertNotIn("source-warn", card)
            self.assertIn(outcome, card)
            self.assertNotIn("could not be verified", card)
        off = self.card("spycloud_dry_run_off", "dry_run")
        self.assertIn("SpyCloud is OFF (default is ON)", off)
        self.assertIn("source-warn", off)
        self.assertNotIn("could not be verified", off)
        failed = self.card("spycloud_readback_still_off")
        self.assertIn("SpyCloud not verified", failed)
        self.assertNotIn("could not be verified", failed)  # has its own message
        self.assertIn("Run the SpyCloud check again", failed)

    def test_states_load_and_legacy_outcomes_still_load(self):
        self.path.write_text(json.dumps({
            "CO-0679": {"outcome": "spycloud_on_verified", "mode": "standalone", "observed_at": "2026-10-08T12:00:00"},
            "CO-0757": {"outcome": "spycloud_off_verified", "mode": "after_create", "observed_at": "2026-10-05T12:00:00"},
            "CO-0728": {"outcome": "spycloud_saved_unverified", "mode": "standalone", "observed_at": "2026-10-08T12:00:00"},
        }), encoding="utf-8")
        states = dashboard.attended_spycloud_states()
        self.assertEqual({r: (s["state"], s["ok"]) for r, s in states.items()},
                         {"CO-0679": ("on", True), "CO-0757": ("off", True), "CO-0728": ("unknown", False)})

    def test_dashboard_gains_no_write_path(self):
        card = self.card("spycloud_dry_run_off", "dry_run")
        self.assertNotIn("spycloud-on", card)
        with patch.object(dashboard, "_start_runner_mode", return_value=True) as start:
            dashboard.start_attended_spycloud_check("CO-0679")
        start.assert_called_once_with("--co", "CO-0679", "--spycloud-off")
        self.assertNotIn("/attended/spycloud-on", dashboard.POST_ROUTES)
        self.assertEqual(dashboard.SESSION_GATED_ROUTES["/attended/spycloud-check"], "read")


if __name__ == "__main__":
    unittest.main()
