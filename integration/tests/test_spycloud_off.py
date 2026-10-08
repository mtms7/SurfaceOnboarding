"""Manual SpyCloud tool (owner decision 2026-10-08: SpyCloud stays ON; OFF is informational): runner, disabled hook,
state file, dashboard, validation.

No network, no browser: every page is a recording fake, every file is a temporary one.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import datetime
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.tests.test_attended_open_onboardings_dashboard import _surface_evaluation

ORIGIN = runner.DEVELOPMENT_ORIGIN
ID = "a" * 24
UUID = "b" * 32
OTHER_ID = "z" * 24
HAZARDS = ("Grid_Access", "Scan_Now", "Stop_Scan", "Delete")


def edit_url(tenant_id=ID, origin=ORIGIN):
    return f"{origin}/api/v1/backoffice/account/{tenant_id}/edit"


class _Response:
    def __init__(self, url, status=200, method="POST"):
        self.url, self.status = url, status
        self.request = type("R", (), {"method": method})()


class _Locator:
    def __init__(self, page, name, count=1):
        self.page, self.name, self._count = page, name, count

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    def filter(self, **_kw):
        return self

    def count(self):
        return self._count

    def click(self, **_kw):
        self.page.clicked.append(self.name)
        if self.name == "checkbox" and self.page.toggle_works:
            self.page.checked = not self.page.checked
        if self.name in self.page.click_fails:
            raise TimeoutError(self.name)

    def is_checked(self):
        if self.page.checked_error:
            raise RuntimeError("unreadable")
        return self.page.checked


class FakePage:
    """Records every locator, click and key; behaviour is set with attributes."""

    def __init__(self, *, checked=True, expanders=2, checkboxes=1, confirm=1, cancel=1, response="ok",
                 toggle_works=True, checked_error=False, click_fails=(), url=ORIGIN + "/backoffice/tenantManagement"):
        self.url, self.checked, self.expanders, self.checkboxes = url, checked, expanders, checkboxes
        self.confirm, self.cancel, self.toggle_works = confirm, cancel, toggle_works
        self.checked_error, self.click_fails = checked_error, set(click_fails)
        self.response = _Response(edit_url()) if response == "ok" else response
        self.selectors, self.texts, self.roles, self.clicked, self.keys = [], [], [], [], []
        self.keyboard = type("K", (), {"press": lambda _s, key: self.keys.append(key)})()

    def locator(self, selector):
        self.selectors.append(selector)
        if selector == runner.SPYCLOUD_CHECKBOX_SELECTOR:
            return _Locator(self, "checkbox", self.checkboxes)
        return _Locator(self, selector)

    def get_by_text(self, text, exact=False):
        self.texts.append(text)
        return _Locator(self, "expander", self.expanders)

    def get_by_role(self, role, name="", exact=False):
        self.roles.append((role, name))
        return _Locator(self, name, {"Confirm": self.confirm, "Cancel": self.cancel}.get(name, 0))

    def wait_for_timeout(self, _ms):
        pass

    def expect_response(self, predicate, timeout=0):
        response = self.response
        if response is None:
            raise TimeoutError()
        assert predicate(response)
        return type("C", (), {"__enter__": lambda _s: _s, "__exit__": lambda _s, *a: False, "value": response})()


def rows(*, enabled, extra=0):
    row = {"id": ID, "accountUuid": UUID, "accountName": "Tango - CE Only",
           "leakedCredentialsSettings": {"spyCloudSettings": {"enabled": enabled}}}
    others = [{"id": f"{n:024d}", "accountUuid": f"{n:032d}"} for n in range(1, extra + 1)]
    return runner.TenantSearchResult([row, *others], 1 + extra)


def search_results(*results):
    """_search_tenants side effects: pre-read, then (after a save) the read-after-write."""
    return patch.object(runner, "_search_tenants", side_effect=list(results))


def run_off(page, *, confirm_write, results, **kw):
    with search_results(*results):
        return runner.set_spycloud_off(page, "Tango - CE Only", kw.pop("expected_id", ID), expected_uuid=UUID,
                                       confirm_write=confirm_write, **kw)


class SetSpycloudOffTests(unittest.TestCase):
    def assertSafe(self, page):
        """Only allow-listed row actions, never a hazard, never another field's control."""
        for selector in page.selectors:
            for hazard in HAZARDS:
                self.assertNotIn(hazard, selector)
        row_actions = [s for s in page.selectors if "data-am" in s]
        self.assertEqual(row_actions, [runner.SPYCLOUD_EDIT_SELECTOR])
        self.assertEqual({s for s in page.selectors if "input" in s}, {runner.SPYCLOUD_CHECKBOX_SELECTOR})
        self.assertEqual(page.clicked.count("checkbox") <= 1, True)

    def test_dry_run_reports_on_and_cancels_without_touching_anything(self):
        page = FakePage(checked=True)
        self.assertEqual(run_off(page, confirm_write=False, results=[rows(enabled=True)]), "spycloud_dry_run_on")
        self.assertNotIn("checkbox", page.clicked)
        self.assertNotIn("Confirm", page.clicked)
        self.assertEqual(page.clicked[-1], "Cancel")
        self.assertEqual(page.texts, [runner.SPYCLOUD_ADVANCED_TEXT])
        self.assertEqual(page.clicked.count("expander"), 2)  # both Advanced options sections
        self.assertTrue(page.checked)
        self.assertSafe(page)

    def test_already_off_cancels_and_never_saves_even_with_confirm_write(self):
        for confirm_write in (False, True):
            page = FakePage(checked=False, response=None)
            self.assertEqual(run_off(page, confirm_write=confirm_write, results=[rows(enabled=False)]),
                             "spycloud_already_off")
            self.assertEqual(page.clicked[-1], "Cancel")
            self.assertNotIn("Confirm", page.clicked)
            self.assertNotIn("checkbox", page.clicked)
            self.assertSafe(page)

    def test_confirmed_save_unchecks_only_spycloud_confirms_and_verifies_the_row(self):
        page = FakePage(checked=True)
        outcome = run_off(page, confirm_write=True, results=[rows(enabled=True), rows(enabled=False)])
        self.assertEqual(outcome, "spycloud_off_verified")
        self.assertFalse(page.checked)
        self.assertEqual(page.clicked.count("checkbox"), 1)
        self.assertEqual(page.clicked[-1], "Confirm")
        self.assertNotIn("Cancel", page.clicked)
        self.assertEqual(page.keys, [])
        self.assertEqual(page.roles, [("button", "Confirm")])
        self.assertSafe(page)
        # The click order: row, row menu, Edit, expanders, checkbox, Confirm. Nothing else.
        self.assertEqual(page.clicked, ["tr, [role='row']", runner.SCAN_EXEC_ROW_MENU_SELECTOR,
                                        runner.SPYCLOUD_EDIT_SELECTOR, "expander", "expander", "checkbox", "Confirm"])

    def test_edit_post_for_another_tenant_fails_loudly(self):
        page = FakePage(response=_Response(edit_url(OTHER_ID)))
        self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True)]), "spycloud_save_id_mismatch")

    def test_non_2xx_is_a_failed_save_and_401_403_are_session_expiry(self):
        for status in (400, 500):
            page = FakePage(response=_Response(edit_url(), status))
            self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True)]), "spycloud_save_failed")
        for status in (401, 403):
            page = FakePage(response=_Response(edit_url(), status))
            with self.assertRaises(runner.LeonardoSessionExpired):
                run_off(page, confirm_write=True, results=[rows(enabled=True)])

    def test_no_reply_is_uncertain_and_never_clicks_confirm_twice(self):
        page = FakePage(response=None)
        self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True)]), "spycloud_save_no_signal")
        self.assertLessEqual(page.clicked.count("Confirm"), 1)
        self.assertEqual(page.clicked[-1], "Cancel")  # the form is closed, never re-submitted

    def test_only_the_apps_own_post_on_the_development_origin_counts(self):
        for response in (_Response(edit_url(origin="https://leonardo.app.pentera.io")), _Response(edit_url(), method="GET"),
                         _Response(ORIGIN + "/api/v1/backoffice/account/x/other")):
            self.assertFalse(runner._is_spycloud_edit_response(response))
        self.assertTrue(runner._is_spycloud_edit_response(_Response(edit_url())))

    def test_readback_still_on_missing_or_unavailable(self):
        for second, code in ((rows(enabled=True), "spycloud_readback_still_on"),
                             (rows(enabled="yes"), "spycloud_readback_missing"),
                             ):
            page = FakePage()
            self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True), second]), code)

    def test_a_failed_reread_after_a_2xx_save_is_saved_unverified_after_one_retry(self):
        # Live 2026-10-05: the save returned 200 but the re-read failed; that must never read as "not written".
        for failed in (None, runner.TenantSearchResult([], 0)):
            page = FakePage()
            code = run_off(page, confirm_write=True, results=[rows(enabled=True), failed, failed])
            self.assertEqual(code, "spycloud_saved_unverified")
            self.assertEqual(runner.spycloud_write_label(code, True), "attempted_unverified")
        page = FakePage()
        self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True), None, rows(enabled=False)]),
                         "spycloud_off_verified")

    def test_reason_codes_before_any_save_cancel_the_form(self):
        cases = (
            (dict(expanders=0, checkboxes=0), "spycloud_advanced_options_unavailable"),
            (dict(expanders=2, checkboxes=0), "spycloud_checkbox_missing"),
            (dict(checkboxes=2), "spycloud_checkbox_ambiguous"),
            (dict(checked_error=True), "spycloud_checkbox_unreadable"),
            (dict(toggle_works=False), "spycloud_uncheck_failed"),
            (dict(confirm=0), "spycloud_confirm_unavailable"),
            (dict(confirm=2), "spycloud_confirm_unavailable"),
        )
        for options, code in cases:
            page = FakePage(**options)
            with self.subTest(code=code, options=options):
                self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True)]), code)
                self.assertEqual(page.clicked[-1], "Cancel")
                self.assertNotIn("Confirm", page.clicked)
                self.assertSafe(page)

    def test_edit_cannot_be_opened(self):
        page = FakePage(click_fails=(runner.SPYCLOUD_EDIT_SELECTOR,))
        self.assertEqual(run_off(page, confirm_write=True, results=[rows(enabled=True)]), "spycloud_edit_unavailable")
        self.assertEqual(page.clicked[-1], "Cancel")

    def test_a_form_that_will_not_close_is_reported_when_nothing_was_saved(self):
        page = FakePage(checked=False, cancel=0)
        self.assertEqual(run_off(page, confirm_write=False, results=[rows(enabled=False)]), "spycloud_cancel_unavailable")
        self.assertEqual(page.keys, ["Escape"])
        page = FakePage(checked=True, cancel=0)
        self.assertEqual(run_off(page, confirm_write=False, results=[rows(enabled=True)]), "spycloud_cancel_unavailable")

    def test_tenant_must_be_exactly_one_row_with_the_expected_ids(self):
        for results, code in (([runner.TenantSearchResult([], 0)], "spycloud_tenant_not_found"),
                              ([None], "spycloud_readback_unavailable"),
                              ([rows(enabled=True, extra=1)], "spycloud_row_ambiguous"),
                              ([runner.TenantSearchResult([{**rows(enabled=True).rows[0], "id": OTHER_ID}], 1)],
                               "spycloud_tenant_not_found")):
            page = FakePage()
            self.assertEqual(run_off(page, confirm_write=True, results=results), code)
            self.assertEqual(page.clicked, [])  # no click at all before the tenant is proven
        page = FakePage()  # a uuid that differs is a different tenant
        with search_results(rows(enabled=True)):
            self.assertEqual(runner.set_spycloud_off(page, "Tango - CE Only", ID, expected_uuid="c" * 32,
                                                     confirm_write=True), "spycloud_tenant_not_found")

    def test_production_origin_or_malformed_id_is_refused_before_any_page_action(self):
        for page, tenant_id in ((FakePage(url="https://leonardo.app.pentera.io/backoffice/tenantManagement"), ID),
                                (FakePage(), "bad id!"), (FakePage(), "")):
            with patch.object(runner, "_search_tenants", side_effect=AssertionError("no search")):
                self.assertEqual(runner.set_spycloud_off(page, "Tango", tenant_id, confirm_write=True),
                                 "spycloud_environment_not_supported")
            self.assertEqual((page.selectors, page.clicked), ([], []))

    def test_only_details_and_edit_may_be_opened_from_the_row_menu(self):
        page = FakePage()
        for hazard in ('[data-am="Button-Grid_Access"]', '[data-am="Button-Grid_Scan_Now"]',
                       '[data-am="Button-Grid_Stop_Scan"]', '[data-am="Button-Grid_Delete"]'):
            with self.assertRaises(ValueError):
                runner._open_row_action(page, "Tango", hazard)
        self.assertEqual(page.clicked, [])
        source = inspect.getsource(runner)
        spycloud_block = source[source.index("# --- Manual SpyCloud tool"):source.index("# --- Leonardo tenant inventory")]
        for hazard in HAZARDS:
            self.assertNotIn(hazard + '"', spycloud_block.replace("Delete) is a hazard", ""))


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "attended_spycloud.json"
        patcher = patch.object(runner, "SPYCLOUD_STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_records_replace_atomically_and_flag_warnings(self):
        now = datetime(2026, 10, 5, 12, 0, 0)
        runner.write_spycloud_state("CO-0757", "spycloud_dry_run_on", "dry_run", now)
        runner.write_spycloud_state("CO-0679", "spycloud_off_verified", "standalone", now)
        runner.write_spycloud_state("CO-0757", "spycloud_off_verified", "standalone", now)
        state = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({key: value["warning"] for key, value in state.items()}, {"CO-0757": False, "CO-0679": False})
        runner.write_spycloud_state("CO-0762", "spycloud_save_failed", "after_create", now)
        self.assertTrue(json.loads(self.path.read_text(encoding="utf-8"))["CO-0762"]["warning"])
        # warning = "flag not verified": the expected ON outcome is no warning, only a failure or unknown is
        self.assertEqual(sorted(runner.SPYCLOUD_OK_OUTCOMES),
                         ["spycloud_already_off", "spycloud_already_on", "spycloud_dry_run_off", "spycloud_dry_run_on",
                          "spycloud_off_verified", "spycloud_on_verified"])
        self.assertEqual([runner.spycloud_state_of(o) for o in ("spycloud_dry_run_on", "spycloud_already_off",
                                                              "spycloud_off_verified", "spycloud_save_failed", "x")],
                         ["on", "off", "off", "unknown", "unknown"])
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        for reference, outcome, mode in (("bad", "spycloud_x", "dry_run"), ("CO-0757", "<b>", "dry_run"),
                                         ("CO-0757", "spycloud_x", "live")):
            with self.assertRaises(ValueError):
                runner.write_spycloud_state(reference, outcome, mode, now)


class AfterCreateHookTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "attended_spycloud.json"
        patcher = patch.object(runner, "SPYCLOUD_STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def hook(self, engine=runner.CE_ENGINE):
        runner._spycloud_after_create(object(), "CO-0757", engine, "Tango", ID, UUID)

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def test_disabled_by_owner_decision_no_automatic_write_after_create(self):
        self.assertIs(runner.SPYCLOUD_AFTER_CREATE_ENABLED, False)  # owner, 2026-10-08: Leonardo's default ON stays
        with patch.object(runner, "set_spycloud_off", side_effect=AssertionError("must not run")), \
                patch.object(runner, "run_spycloud_off", side_effect=AssertionError("must not run")):
            for engine in (runner.CE_ENGINE, runner.CASE3_ENGINE, runner.SURFACE_ENGINE):
                self.hook(engine)
        self.assertEqual(self.stored(), {})

    def test_enabled_runs_on_ce_and_case3_only_never_on_surface_only(self):
        with patch.object(runner, "SPYCLOUD_AFTER_CREATE_ENABLED", True), \
                patch.object(runner, "set_spycloud_off", return_value="spycloud_off_verified") as call:
            self.hook(runner.SURFACE_ENGINE)
            call.assert_not_called()
            self.hook(runner.CE_ENGINE)
            self.hook(runner.CASE3_ENGINE)
            self.assertEqual(call.call_count, 2)
            self.assertTrue(call.call_args.kwargs["confirm_write"])
        self.assertEqual(self.stored()["CO-0757"]["outcome"], "spycloud_off_verified")
        self.assertFalse(self.stored()["CO-0757"]["warning"])
        self.assertEqual(self.stored()["CO-0757"]["mode"], "after_create")

    def test_a_failure_is_a_recorded_warning_and_never_raises(self):
        for effect, expected in ((RuntimeError("boom"), "spycloud_hook_error"),
                                 (runner.LeonardoSessionExpired(), "leonardo_session_expired"),
                                 ("spycloud_save_failed", "spycloud_save_failed")):
            kwargs = {"return_value": effect} if isinstance(effect, str) else {"side_effect": effect}
            log = runner.RunLog("CO-0757", "create")
            with patch.object(runner, "SPYCLOUD_AFTER_CREATE_ENABLED", True), \
                    patch.object(runner, "set_spycloud_off", **kwargs), patch.object(runner, "_ACTIVE_RUN_LOG", log):
                self.hook()
            self.assertEqual(self.stored()["CO-0757"]["outcome"], expected)
            self.assertTrue(self.stored()["CO-0757"]["warning"])
            self.assertIn(("spycloud_warning", "not_verified"), [(e["step"], e["outcome"]) for e in log.events])

    def test_off_is_an_informational_note_never_a_warning(self):
        log = runner.RunLog("CO-0757", "create")
        with patch.object(runner, "_log", return_value=log):
            runner._record_spycloud("CO-0757", "spycloud_already_off", "standalone")
            runner._record_spycloud("CO-0758", "spycloud_dry_run_on", "dry_run")
        steps = [e["step"] for e in log.events]
        self.assertIn("spycloud_note", steps)
        self.assertNotIn("spycloud_warning", steps)
        self.assertFalse(self.stored()["CO-0757"]["warning"] or self.stored()["CO-0758"]["warning"])

    def test_the_hook_runs_after_the_readback_is_recorded_and_cannot_change_the_result(self):
        source = inspect.getsource(runner._run)
        evidence = source.index("write_readback_evidence(reference, surface_account_id")
        hook = source.index("_spycloud_after_create(page, reference, contract.engine")
        final = source.index('return _finish(reference, acknowledged_revision, "readback_verified")')
        self.assertLess(evidence, hook)
        self.assertLess(hook, final)
        self.assertNotIn("= _spycloud_after_create", source)  # its value is never used


class StandaloneRunTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.state = directory / "attended_spycloud.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: contextlib.nullcontext(object())
        package = types.ModuleType("playwright")
        package.sync_api = fake

        @contextlib.contextmanager
        def attended(_playwright):
            yield types.SimpleNamespace(on=lambda *a, **k: None, url=ORIGIN + "/backoffice/tenantManagement")
        self.stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": fake}))
        for name, value in (("SPYCLOUD_STATE_PATH", self.state), ("RUN_LOG_PATH", directory / "log.json")):
            self.stack.enter_context(patch.object(runner, name, value))
        self.stack.enter_context(patch.object(runner, "_attended_page", attended))
        self.stack.enter_context(patch.object(runner, "_readback_ids", return_value=(ID, UUID)))

    def run_co(self, route=runner.CE_ENGINE, **kw):
        with patch.object(runner, "_validation_route", return_value=(route, "Tango - CE Only")):
            return runner.run_spycloud_off("CO-0757", **kw)

    def test_a_verified_ce_renewal_mirror_is_eligible(self):
        unsupported = runner.SurfaceSourceError("validation_route_unsupported")
        record = {"CO-0767": {"mirror_of_production": True, "status": "verified"}}
        sf_row = [{"Name": "CO-0767", "Account_Name__c": "A2A", "Onboarding_Product__c": "Surface & Credential Exposure",
                   "Onboarding_Type__c": "Renewal of Existing Product"}]
        with patch.object(runner, "_validation_route", side_effect=unsupported),                 patch.object(runner, "_mirror_records", return_value=record),                 patch.object(runner, "_sf_records", return_value=sf_row),                 patch.object(runner, "set_spycloud_off", return_value="spycloud_dry_run_on") as call:
            self.assertEqual(runner.run_spycloud_off("CO-0767"), "spycloud_dry_run_on")
        self.assertEqual(call.call_args.args[1], "A2A")

    def test_no_mirror_or_a_surface_only_renewal_stays_unsupported(self):
        unsupported = runner.SurfaceSourceError("validation_route_unsupported")
        surface_only = [{"Name": "CO-0767", "Account_Name__c": "A2A", "Onboarding_Product__c": "Surface",
                         "Onboarding_Type__c": "Renewal of Existing Product"}]
        for records, rows in (({}, surface_only),
                              ({"CO-0767": {"mirror_of_production": True, "status": "create_attempted"}}, surface_only),
                              ({"CO-0767": {"mirror_of_production": True, "status": "verified"}}, surface_only)):
            with patch.object(runner, "_validation_route", side_effect=unsupported),                     patch.object(runner, "_mirror_records", return_value=records),                     patch.object(runner, "_sf_records", return_value=rows),                     patch.object(runner, "set_spycloud_off", side_effect=AssertionError("untouched")):
                self.assertEqual(runner.run_spycloud_off("CO-0767"), "validation_route_unsupported")

    def test_production_is_refused_before_anything_else(self):
        with patch.object(runner, "_readback_ids", side_effect=AssertionError("untouched")), \
                patch.object(runner, "set_spycloud_off", side_effect=AssertionError("untouched")):
            for env in ("prod", "prod-clone", "", "DEV"):
                self.assertEqual(runner.run_spycloud_off("CO-0757", confirm_write=True, env_name=env),
                                 "spycloud_environment_not_supported")
        self.assertFalse(self.state.exists())

    def test_default_is_a_dry_run_and_the_confirm_flag_is_passed_through(self):
        for kwargs, flag, mode in (({}, False, "dry_run"), ({"confirm_write": True}, True, "standalone")):
            with patch.object(runner, "set_spycloud_off", return_value="spycloud_dry_run_on") as call:
                self.assertEqual(self.run_co(**kwargs), "spycloud_dry_run_on")
            self.assertIs(call.call_args.kwargs["confirm_write"], flag)
            self.assertEqual(call.call_args.args[1:3], ("Tango - CE Only", ID))
            self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["CO-0757"]["mode"], mode)

    def test_surface_only_route_and_not_onboarded_are_refused(self):
        with patch.object(runner, "set_spycloud_off", side_effect=AssertionError("untouched")):
            self.assertEqual(self.run_co(runner.SURFACE_ENGINE, confirm_write=True), "spycloud_route_not_applicable")
            with patch.object(runner, "_readback_ids", return_value=None):
                self.assertEqual(self.run_co(confirm_write=True), "spycloud_not_onboarded")
            self.assertEqual(runner.run_spycloud_off("not-a-co"), "invalid_co_reference")

    def test_session_expiry_is_reported_and_not_recorded_as_an_outcome(self):
        with patch.object(runner, "set_spycloud_off", side_effect=runner.LeonardoSessionExpired()):
            self.assertEqual(self.run_co(confirm_write=True), "leonardo_session_expired")
        self.assertFalse(self.state.exists())


class CommandLineTests(unittest.TestCase):
    def main(self, *argv):
        with patch.object(sys, "argv", ["runner", *argv]), patch("builtins.print") as printed:
            code = runner.main()
        return code, json.loads(printed.call_args.args[0])

    def test_dry_run_is_the_default_and_confirm_write_is_explicit(self):
        with patch.object(runner, "run_spycloud_off", return_value="spycloud_dry_run_on") as call:
            code, report = self.main("--co", "CO-0757", "--spycloud-off")
            self.assertEqual(call.call_args.kwargs, {"confirm_write": False, "env_name": "dev"})
            self.assertEqual(report["leonardo_write"], "not_performed")
            self.main("--co", "CO-0757", "--spycloud-off", "--confirm-write")
            self.assertEqual(call.call_args.kwargs, {"confirm_write": True, "env_name": "dev"})
            self.main("--co", "CO-0757", "--spycloud-off", "--confirm-write", "--env", "prod")
            self.assertEqual(call.call_args.kwargs["env_name"], "prod")  # the function refuses it

    def test_confirm_write_alone_or_without_co_is_a_usage_error(self):
        for argv in (["--co", "CO-0757", "--confirm-write"], ["--spycloud-off"], ["--confirm-write"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.main(*argv)


class WriteLabelTests(unittest.TestCase):
    def test_a_run_that_stopped_before_confirm_never_claims_a_write(self):
        for result in ("playwright_runtime_unavailable", "spycloud_tenant_not_found", "spycloud_dry_run_on",
                       "spycloud_already_off", "spycloud_checkbox_missing"):
            self.assertEqual(runner.spycloud_write_label(result, True), "not_performed")

    def test_after_confirm_outcomes_are_attempted_unverified_and_only_verified_is_verified(self):
        self.assertEqual(runner.spycloud_write_label("spycloud_save_failed", True), "attempted_unverified")
        self.assertEqual(runner.spycloud_write_label("spycloud_readback_still_on", True), "attempted_unverified")
        self.assertEqual(runner.spycloud_write_label("spycloud_save_failed", False), "not_performed")
        self.assertEqual(runner.spycloud_write_label("spycloud_off_verified", True), "verified")


class ValidationCheckTests(unittest.TestCase):
    def row(self, enabled):
        return {"enabled": True, "isDeleted": False, "accountLicense": {"enabled": True},
                "leakedCredentialsSettings": {"spyCloudSettings": {"enabled": enabled}}}

    def spy(self, checks):
        return [c for c in checks if c["check"].startswith("SpyCloud")]

    def test_lc_routes_get_an_informational_check_never_a_drift(self):
        for route in (runner.CE_ENGINE, runner.CASE3_ENGINE):
            on = self.spy(runner.validate_row(self.row(True), None, None, route))
            self.assertEqual([(c["check"], c["status"]) for c in on],
                             [("SpyCloud is ON (default)", "ok")])
            off = self.spy(runner.validate_row(self.row(False), None, None, route))
            self.assertEqual([(c["check"], c["status"]) for c in off],
                             [("SpyCloud is OFF (default is ON)", "warn")])
            unknown = self.spy(runner.validate_row(self.row("yes"), None, None, route))
            self.assertEqual([c["status"] for c in unknown], ["unknown"])
        every = runner.validate_row(self.row(True), None, None, runner.CE_ENGINE)
        self.assertNotIn("drift", {c["status"] for c in every})

    def test_surface_only_and_unrouted_validation_has_no_spycloud_check(self):
        for route in (runner.SURFACE_ENGINE, None):
            self.assertEqual(self.spy(runner.validate_row(self.row(True), None, None, route)), [])

    def test_a_warning_is_not_a_validation_drift(self):
        stored = {}
        with patch.object(runner, "write_validation", lambda *a, **k: stored.update(checks=a[2])), \
                patch.object(runner, "write_scan_status"), \
                patch.object(runner, "_salesforce_primary_user", return_value=None):
            result = runner._record_validation("CO-0757", runner.CE_ENGINE, self.row(False), None, "", None)
        self.assertEqual(result, "validation_recorded")
        self.assertIn("warn", {c["status"] for c in stored["checks"]})


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "attended_spycloud.json"
        patcher = patch.object(dashboard, "SPYCLOUD_STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    entry = {"outcome": "spycloud_save_failed", "mode": "after_create", "observed_at": "2026-10-05T12:00:00",
             "warning": True}

    def test_an_unverified_check_is_visible_and_never_asks_for_off(self):
        self.write({"CO-0757": self.entry})
        card = dashboard._spycloud_section("CO-0757")
        self.assertIn("SpyCloud not verified", card)
        self.assertIn("spycloud_save_failed", card)
        self.assertIn("/attended/spycloud-check", card)
        self.assertIn("after create", card)
        self.assertNotIn("run SpyCloud off", card)
        self.assertNotIn("must be OFF", card)

    def test_on_is_the_expected_state_and_off_is_informational(self):
        self.write({"CO-0757": {**self.entry, "outcome": "spycloud_dry_run_on", "warning": False}})
        on = dashboard._spycloud_section("CO-0757")
        self.assertIn("SpyCloud is ON (default)", on)
        self.assertIn("source-ready", on)
        self.assertNotIn("still ON", on)
        self.assertNotIn("source-warn", on)
        self.write({"CO-0757": {**self.entry, "outcome": "spycloud_off_verified", "warning": False}})
        off = dashboard._spycloud_section("CO-0757")
        self.assertIn("SpyCloud is OFF (default is ON)", off)
        self.assertIn("informational", off)
        self.assertIn("source-warn", off)
        self.assertNotIn("still ON", off)
        none = dashboard._spycloud_section("CO-0999")
        self.assertIn("Not checked yet", none)
        self.assertIn("Check SpyCloud (read-only)", none)
        for card in (on, off, none):
            self.assertNotIn("must be OFF", card)
            self.assertNotIn("requires SpyCloud OFF", card)
            self.assertNotIn("run SpyCloud off", card)

    def test_legacy_records_load_and_derive_their_state_from_the_outcome(self):
        # Before 2026-10-08 an ON dry run stored warning=True and "ok" meant verified OFF.
        self.write({"CO-0757": {**self.entry, "outcome": "spycloud_dry_run_on", "mode": "dry_run", "warning": True},
                    "CO-0758": {**self.entry, "outcome": "spycloud_already_off", "mode": "standalone", "warning": False},
                    "CO-0759": {**self.entry, "outcome": "spycloud_save_failed", "mode": "after_create"},
                    "CO-0760": {k: v for k, v in self.entry.items() if k != "warning"}})
        states = dashboard.attended_spycloud_states()
        self.assertEqual({r: (s["state"], s["ok"]) for r, s in states.items()},
                         {"CO-0757": ("on", True), "CO-0758": ("off", True), "CO-0759": ("unknown", False),
                          "CO-0760": ("unknown", False)})

    def test_output_is_escaped_and_malformed_state_is_dropped(self):
        self.write({"CO-0757": {**self.entry, "outcome": "<script>alert(1)</script>"},
                    "CO-0758": {**self.entry, "mode": "<b>"}, "CO-0759": {**self.entry, "observed_at": "soon"},
                    "bad": self.entry, "CO-0760": self.entry})
        self.assertEqual(list(dashboard.attended_spycloud_states()), ["CO-0760"])
        hostile = dashboard._spycloud_section('CO-1"><script>x</script>')
        self.assertNotIn("<script>", hostile)
        self.assertNotIn('"><script', hostile)
        self.assertNotIn("<script", dashboard._spycloud_section("CO-0760").lower())

    def test_check_button_never_passes_confirm_write(self):
        with patch.object(dashboard, "_start_runner_mode", return_value=True) as start:
            self.assertTrue(dashboard.start_attended_spycloud_check("CO-0757"))
            self.assertFalse(dashboard.start_attended_spycloud_check("nope"))
        start.assert_called_once_with("--co", "CO-0757", "--spycloud-off")

    def test_post_route_is_registered_and_session_gated_read_only(self):
        self.assertIn("/attended/spycloud-check", dashboard.POST_ROUTES)
        self.assertEqual(dashboard.SESSION_GATED_ROUTES["/attended/spycloud-check"], "read")
        self.assertNotIn("confirm", "/attended/spycloud-check")

    def test_handler_starts_the_dry_run_for_an_onboarded_co_only(self):
        class Request:
            path = "/attended/spycloud-check"
            def __init__(self):
                self.pages, self.redirects = [], []
            def send_page(self, status, page):
                self.pages.append(int(status))
            def send_redirect(self, location):
                self.redirects.append(location)
        for onboarded, started, expected in ((True, True, ("redirect", "/co/CO-0757?spycloud=started")),
                                             (False, True, ("page", 409)), (True, False, ("page", 503))):
            request = Request()
            with self.subTest(onboarded=onboarded, started=started), \
                    patch.object(dashboard, "login_required", return_value=False), \
                    patch.object(dashboard, "post_form", return_value={"reference": ["CO-0757"]}), \
                    patch.object(dashboard, "action_readiness_problem", return_value=None), \
                    patch.object(dashboard, "attended_leonardo_readbacks",
                                 return_value={"CO-0757": {}} if onboarded else {}), \
                    patch.object(dashboard, "start_attended_spycloud_check", return_value=started):
                dashboard.Handler.do_POST(request)
            self.assertEqual(("redirect", request.redirects[0]) if request.redirects else ("page", request.pages[0]),
                             expected)

    def co_page(self, product, onboarding_type):
        ids = {"surface_account_id": "0123456789abcdef01234567", "account_uuid": "0123456789abcdef0123456789abcdef",
               "leonardo_state": "No scan started", "observed_on": "2026-10-05", "source": runner.READBACK_SOURCE}
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Product__c": product,
               "Onboarding_Type__c": onboarding_type}
        with patch.object(dashboard, "attended_leonardo_readbacks", return_value={"CO-0757": ids}), \
                patch.object(dashboard, "evaluate_ce_only_fill_preflight", side_effect=dashboard.ReadUnavailable()), \
                patch.object(dashboard, "evaluate_surface_fill_preflight", return_value=_surface_evaluation()), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce")):
            return dashboard.page_detail("CO-0757", row)

    def test_card_is_on_the_ce_and_case3_pages_not_on_surface_only(self):
        self.write({"CO-0757": self.entry})
        ce = self.co_page(dashboard.CE_ROUTE_PRODUCT, dashboard.CE_ROUTE_TYPE)
        case3 = self.co_page(dashboard.CASE3_ROUTE_PRODUCT, dashboard.CASE3_ROUTE_TYPE)
        surface = self.co_page(dashboard.SURFACE_ROUTE_PRODUCT, dashboard.SURFACE_ROUTE_TYPE)
        self.assertIn("SpyCloud not verified", ce)
        self.assertIn("SpyCloud not verified", case3)
        self.assertNotIn("spycloud-title", surface)

    def test_pages_show_no_off_banner_or_step_for_on_and_only_a_note_for_off(self):
        for outcome, banner in (("spycloud_dry_run_on", False), ("spycloud_off_verified", True),
                                ("spycloud_save_failed", False)):
            self.write({"CO-0757": {**self.entry, "outcome": outcome, "warning": False}})
            for product, kind in ((dashboard.CE_ROUTE_PRODUCT, dashboard.CE_ROUTE_TYPE),
                                  (dashboard.CASE3_ROUTE_PRODUCT, dashboard.CASE3_ROUTE_TYPE)):
                page = self.co_page(product, kind)
                with self.subTest(outcome=outcome, product=product):
                    self.assertEqual("<b>SpyCloud is OFF.</b>" in page, banner)
                    for text in ("SpyCloud is still ON", "must be OFF", "requires SpyCloud OFF", "Turn SpyCloud OFF",
                                 "run SpyCloud off", "turns SpyCloud OFF"):
                        self.assertNotIn(text, page)
        self.write({})
        for product, kind in ((dashboard.CE_ROUTE_PRODUCT, dashboard.CE_ROUTE_TYPE),
                              (dashboard.CASE3_ROUTE_PRODUCT, dashboard.CASE3_ROUTE_TYPE)):
            self.assertNotIn("Turn SpyCloud OFF", self.co_page(product, kind))

    def test_validation_card_shows_the_spycloud_note_without_calling_it_a_difference(self):
        checks = [{"group": "Settings", "check": "SpyCloud is OFF (default is ON)", "status": "warn"}]
        entry = {"route": runner.CE_ENGINE, "checks": checks, "plan_note": "", "observed_at": "2026-10-05T12:00:00",
                 "expires_at": "2099-10-05T12:00:00"}
        validation = Path(tempfile.mkdtemp()) / "v.json"
        validation.write_text(json.dumps({"CO-0757": entry}), encoding="utf-8")
        with patch.object(dashboard, "VALIDATION_PATH", validation):
            card = dashboard._validation_section("CO-0757")
        self.assertIn("SpyCloud is OFF (default is ON)", card)
        self.assertIn("Verified, 1 warning(s)", card)
        self.assertNotIn("difference(s) found", card)


class InventoryColumnTests(unittest.TestCase):
    def test_tenants_page_shows_on_off_and_unknown(self):
        from datetime import timezone
        from integration.onboarding import leonardo_inventory as inventory
        from integration.tests.test_leonardo_inventory import tenant_row
        from integration.tests.test_leonardo_inventory_runner import inventory_page_request

        built = []
        for index, value in enumerate((True, False, None), start=1):
            row = tenant_row(index, accountName=f"Tenant {index}")
            row["leakedCredentialsSettings"] = {"spyCloudSettings": {} if value is None else {"enabled": value}}
            built.append(row)
        pages = [(inventory_page_request(i), {"pagination_response": {"total_count": 3, "table_data": [row]}})
                 for i, row in enumerate(built)]
        root = Path(tempfile.mkdtemp())
        inventory.write_snapshot(inventory.snapshot_payload(
            inventory.ENVIRONMENTS["dev"], inventory.assemble_pages(pages, page_size=1),
            datetime.now(timezone.utc)), root)
        with patch.object(dashboard, "inventory_root", return_value=root), \
                patch.object(dashboard, "attended_leonardo_readbacks", return_value={}):
            page = dashboard.render_inventory()
        self.assertIn("SpyCloud</abbr>", page)
        self.assertEqual((page.count("<span class='chip chip-ok'>ON</span>"),
                          page.count("<span class='chip chip-warn'>OFF</span>")), (1, 1))
        order = [page.index(f"Tenant {n}") for n in (1, 2, 3)]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("<script", page.lower())


if __name__ == "__main__":
    unittest.main()
