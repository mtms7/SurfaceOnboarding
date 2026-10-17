"""End-to-end tests of the guarded LIVE production create (docs/53) against a fake page: every gate refusal, the live
duplicate pre-check (name, domain, demo domain, lookup error, a duplicate that appears late), exactly one Confirm click,
no second click after any outcome, the unknown-outcome state, readback mismatch, the UUID never printed, both locks False
means nothing is reachable, the approval is consumed once and revision drift refuses.
No browser, network, Salesforce, Redash or BackOffice: every external step is a fake."""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import session_readiness
from integration.tests import test_production_dry_run as dry
from integration.tests.test_production_create_live import contract_row, reply
import tools.attended_ce_only_playwright as runner

CO, REF, REV, NOW, ORIGIN = dry.CO, dry.REF, dry.REV, dry.NOW, dry.ORIGIN
ROUTE = "case_2_new_ce_only"
UUID = "0123456789abcdef0123456789abcdef"
SOURCE = dry.make_source()
NAME, DOMAIN = SOURCE.tenant_name, SOURCE.email_domain
DAY = date(2026, 10, 16)


def good_row(**over):
    return contract_row(accountName=NAME, accountDomain=DOMAIN, **over)


class ConfirmBtn:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.page.confirm_enabled

    def evaluate(self, js):
        assert js == pdr.HIT_TEST_JS
        return self.page.hit

    def click(self, **kw):
        assert "force" not in kw, "a forced click must never be made"
        self.page.confirm_clicks += 1
        for _ in range(self.page.posts):
            outcome = self.page.fire("POST", ORIGIN + live.CREATE_PATH)
            if outcome == "continued" and self.page.create_status is not None:
                for handler in self.page.response_handlers:
                    handler(SimpleNamespace(request=SimpleNamespace(method="POST"), url=ORIGIN + live.CREATE_PATH,
                                            status=self.page.create_status))
        self.page.form_open = False


class Flip(dict):
    """Search replies by value; a value listed in ``later`` answers ``later[value]`` from its second lookup on."""

    def __init__(self, first, later):
        super().__init__(first)
        self.later, self.calls = later, {}

    def get(self, key, default=None):
        self.calls[key] = self.calls.get(key, 0) + 1
        if key in self.later and self.calls[key] >= 2:
            return self.later[key]
        return super().get(key, default)


class LivePage(dry.FakePage):
    def __init__(self, *, create_status=200, readbacks=None, confirm_enabled=True, posts=1, hit=True, **kw):
        super().__init__(**kw)
        self.create_status, self.confirm_enabled, self.posts, self.hit = create_status, confirm_enabled, posts, hit
        self.confirm_clicks = 0
        self.readbacks = list(readbacks if readbacks is not None else [reply([good_row()])])
        self.readback_calls = 0
        self.response_handlers = []

    def on(self, event, handler):
        if event == "response":
            self.response_handlers.append(handler)

    def get_by_role(self, role, name=None, exact=False):
        if role == "button" and name == "Confirm" and self.form_open:
            return ConfirmBtn(self)
        return super().get_by_role(role, name, exact)

    def evaluate(self, script, arg=None):
        if script == contract.READBACK_JS:
            self.readback_calls += 1
            if self.fire("POST", ORIGIN + arg[0]) == "aborted":
                return {"status": 0, "total": None, "rows": None}
            return self.readbacks.pop(0) if self.readbacks else reply([])
        return super().evaluate(script, arg)


class Harness(dry.Harness):
    def __init__(self, test, **page_kw):
        super().__init__(test)
        self.page = LivePage(**page_kw)
        self.dry_record = True

    def seed_dry_run(self):
        pcr.record(self.records, {"co_reference": CO, "approval_ref": REF, "outcome": "dry_run_passed", "route": ROUTE,
                                  "salesforce_revision": REV}, NOW - timedelta(minutes=30))

    def go(self, *, runner_flag=True, dry_flag=True, go=True, revision=REV, clone=dry.good_clone, source=None, dry_run_record=True):
        if dry_run_record:
            self.seed_dry_run()
        env = dict(dry.UNLOCK)
        out = io.StringIO()
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", runner_flag), \
                mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry_flag), contextlib.redirect_stdout(out):
            line = runner.run_production_create(
                CO, revision, owner_go=go, environ=env, now=NOW, records_parent=self.records,
                approval_path=self.approval_path, load_source=(lambda _r: source) if source else self.load_source,
                clone_facts=clone, open_page=self.open_page, fill_date=lambda *_a: True, date_kept=lambda *_a: True,
                run_day=DAY)
        self.stdout, self.line = out.getvalue(), line
        return json.loads(line)

    def outcomes(self):
        return [pcr.load(p)["outcome"] for p in pcr.list_records(self.records, CO)]

    def loaded_records(self):
        return [pcr.load(p) for p in pcr.list_records(self.records, CO)]

    def confirm_ever_clicked(self):
        return self.page.confirm_clicks


class Base(unittest.TestCase):
    def harness(self, **kw):
        return Harness(self, **kw)

    def no_external_activity(self, h):
        self.assertEqual((h.opened, h.loaded, h.page.confirm_clicks, h.page.fetches), (0, 0, 0, []))


class LockTests(Base):
    def test_constants_are_hard_coded_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, False)

    def test_both_locks_false_reaches_nothing(self):
        h = self.harness()
        result = h.go(runner_flag=False, dry_flag=False)
        self.assertEqual(result["result"], "production_locked_production_dry_run_not_supported")
        self.no_external_activity(h)
        self.assertEqual(h.outcomes(), ["dry_run_passed", "refused"])
        self.assertTrue(h.approval_unused() is None)

    def test_runner_lock_alone_reaches_nothing(self):
        h = self.harness()
        result = h.go(runner_flag=False, dry_flag=True)
        self.assertEqual(result["result"], "production_locked_production_runner_not_supported")
        self.no_external_activity(h)

    def test_dry_run_lock_alone_reaches_nothing(self):
        h = self.harness()
        self.assertEqual(h.go(runner_flag=True, dry_flag=False)["result"],
                         "production_locked_production_dry_run_not_supported")
        self.no_external_activity(h)

    def test_the_real_constants_refuse_without_any_patch(self):
        h = self.harness()
        h.seed_dry_run()
        out = runner.run_production_create(CO, REV, owner_go=True, environ=dict(dry.UNLOCK), now=NOW,
                                           records_parent=h.records, approval_path=h.approval_path,
                                           load_source=h.load_source, open_page=h.open_page)
        self.assertIn("production_locked_production_dry_run_not_supported", out)
        self.no_external_activity(h)

    def test_the_old_dry_run_stubs_still_refuse_to_confirm(self):
        for fn in (runner._pdr_click_confirm, runner._pdr_read_back):
            with self.assertRaises(NotImplementedError):
                fn(None)


class GateRefusalTests(Base):
    def test_owner_go_missing(self):
        for go in (False, None):
            h = self.harness()
            self.assertEqual(h.go(go=go)["result"], "owner_go_missing")
            self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
            self.assertIsNone(h.approval_unused())

    def test_arguments_and_revision_drift(self):
        h = self.harness()
        self.assertEqual(h.go(revision="bad revision!")["result"], "production_create_arguments_invalid")
        self.no_external_activity(h)
        h = self.harness()
        drifted = dry.make_source(source_revision="2026-10-17T00:00:00.000+0000")
        self.assertEqual(h.go(source=drifted)["result"], "approval_revision_drift")
        self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0))
        self.assertIsNone(h.approval_unused())

    def test_a_dry_run_is_required(self):
        h = self.harness()
        self.assertEqual(h.go(dry_run_record=False)["result"], "dry_run_required")
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())

    def test_stale_or_missing_clone_wrong_co_and_other_gates_refuse_before_the_browser(self):
        cases = {
            "clone_stale": lambda s, n: {**dry.good_clone(s, n), "collected_at": NOW - timedelta(hours=7)},
            "clone_missing": lambda s, n: {**dry.good_clone(s, n), "collected_at": None},
            "duplicate_in_production": lambda s, n: {**dry.good_clone(s, n), "duplicate_found": True},
            "multiple_live_paid_tenants": lambda s, n: {**dry.good_clone(s, n), "live_paid_tenant_count": 2},
        }
        for reason, clone in cases.items():
            h = self.harness()
            self.assertEqual(h.go(clone=clone)["result"], reason)
            self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0), reason)
            self.assertIsNone(h.approval_unused())

    def test_prior_unreviewed_attempt_blocks(self):
        for outcome in ("attempt_started", "uncertain_submit", "created_unverified", "readback_verified"):
            h = self.harness()
            h.seed_dry_run()
            pcr.record(h.records, {"co_reference": CO, "approval_ref": REF, "outcome": outcome, "route": ROUTE}, NOW)
            self.assertEqual(h.go(dry_run_record=False)["result"], "prior_attempt_needs_review", outcome)
            self.assertEqual((h.opened, h.page.confirm_clicks), (1, 0), outcome)

    def test_session_expiry_before_the_form(self):
        h = self.harness(session={pdr.AUTHORITIES_PATH: {"status": 401, "names": None},
                                  pdr.ACCOUNT_INFO_PATH: {"status": 200, "names": None}})
        self.assertEqual(h.go()["result"], "production_session_expired")
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertIsNone(h.approval_unused())


class LiveDuplicateTests(Base):
    def expect_refusal(self, h, reason):
        result = h.go()
        self.assertEqual(result["result"], reason)
        self.assertEqual((h.page.confirm_clicks, h.page.form_open), (0, False))
        self.assertIsNone(h.approval_unused())  # nothing consumed
        self.assertEqual(h.outcomes()[-1], "refused")
        self.assertNotIn("attempt_started", h.outcomes())

    def test_duplicate_by_exact_name_with_suffix(self):
        row = {"name": NAME, "domain": "another.example", "alternates": [], "emails": [], "deleted": False}
        self.expect_refusal(self.harness(search={NAME: reply([row])}), "duplicate_in_production")

    def test_duplicate_by_primary_domain_even_without_row_fields(self):
        row = {"name": None, "domain": None, "alternates": [], "emails": [], "deleted": False}
        self.expect_refusal(self.harness(search={DOMAIN: reply([row])}), "duplicate_in_production")

    def test_demo_domain(self):
        self.expect_refusal(self.harness(demo={"status": 200, "hit": True, "count": 4}), "demo_domain_conflict")

    def test_lookup_error_is_never_a_clearance(self):
        broken = {"status": 500, "total": None, "rows": None}
        for search in ({NAME: broken}, {DOMAIN: broken}):
            self.expect_refusal(self.harness(search=search), "duplicate_check_unavailable")
        self.expect_refusal(self.harness(demo={"status": 500, "hit": None, "count": None}), "duplicate_check_unavailable")

    def test_a_duplicate_that_appears_after_the_form_was_filled_stops_before_consume_and_click(self):
        row = {"name": "Someone Else", "domain": DOMAIN, "alternates": [], "emails": [], "deleted": False}
        h = self.harness(search=Flip({"unused": reply([])}, {DOMAIN: reply([row])}))
        self.expect_refusal(h, "duplicate_in_production")
        self.assertGreaterEqual(h.page.search.calls[DOMAIN], 2)
        self.assertNotIn("Confirm", h.page.clicks)

    def test_session_lost_at_the_final_check(self):
        h = self.harness()
        calls = {"n": 0}
        original = h.page.session.get

        def flaky(path, default=None):
            calls["n"] += 1
            if calls["n"] > 2 and path == pdr.AUTHORITIES_PATH:
                return {"status": 401, "names": None}
            return original(path, default)
        h.page.session = SimpleNamespace(get=flaky)
        result = h.go()
        self.assertIn(result["result"], ("production_session_expired",))
        self.assertEqual(h.page.confirm_clicks, 0)

    def test_a_stray_create_post_during_the_fill_is_blocked(self):
        h = self.harness(write_on_select=True)
        self.assertEqual(h.go()["result"], "write_request_blocked")
        self.assertEqual(h.page.confirm_clicks, 0)
        self.assertIsNone(h.approval_unused())


class CreateTests(Base):
    def test_happy_path_one_click_verified_and_the_uuid_is_never_printed(self):
        h = self.harness()
        result = h.go()
        self.assertEqual(result["result"], "created_verified")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 1))
        self.assertEqual(h.outcomes().count("attempt_started"), 1)
        self.assertEqual(sorted(h.outcomes()), sorted(["dry_run_passed", "attempt_started", "readback_verified"]))
        self.assertEqual(h.approval_unused(), "approval_consumed")
        self.assertEqual(len([r for r in h.page.routed if r.request.method == "POST" and r.request.url.endswith(live.CREATE_PATH)]), 1)
        self.assertNotIn(UUID, h.line)
        self.assertNotIn(UUID, h.stdout)
        self.assertEqual(result["salesforce_writeback"], "not_performed")
        verified = [r for r in h.loaded_records() if r["outcome"] == "readback_verified"][0]
        self.assertEqual(verified["account_uuid"], UUID)  # D2/D3: the redacted record is where it is kept
        self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATED_VERIFIED)

    def test_a_second_run_never_clicks_again_after_any_outcome(self):
        for page_kw in ({}, {"create_status": None}, {"create_status": 500}, {"readbacks": [reply([good_row(scanningInterval="DAILY")])]}):
            h = self.harness(**page_kw)
            h.go()
            first_clicks = h.page.confirm_clicks
            self.assertEqual(first_clicks, 1, page_kw)
            again = h.go(dry_run_record=False)
            self.assertEqual(h.page.confirm_clicks, first_clicks, page_kw)
            self.assertIn(again["result"], ("approval_consumed", "prior_attempt_needs_review"), page_kw)

    def test_no_response_is_unknown_and_blocks_the_co_without_a_readback(self):
        for status in (None, 500, 403):
            h = self.harness(create_status=status)
            result = h.go()
            self.assertEqual(result["result"], "create_outcome_unknown", status)
            self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 0), status)
            self.assertEqual(h.approval_unused(), "approval_consumed")
            self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATE_OUTCOME_UNKNOWN)
            self.assertTrue(ui.co_status(h.loaded_records())["blocking"])

    def test_two_create_requests_from_one_click_are_unknown_and_only_one_goes_out(self):
        h = self.harness(posts=2)
        self.assertEqual(h.go()["result"], "create_outcome_unknown")
        sent = [r for r in h.page.routed if r.request.url.endswith(live.CREATE_PATH)]
        self.assertEqual([r.outcome for r in sent], ["continued", "aborted"])

    def test_readback_mismatch_stops_records_and_never_retries(self):
        h = self.harness(readbacks=[reply([good_row(scanningInterval="DAILY", reconLevel="X")])])
        result = h.go()
        self.assertEqual(result["result"], "created_mismatch")
        self.assertEqual(h.page.confirm_clicks, 1)
        record = [r for r in h.loaded_records() if r["outcome"] == "created_unverified"][0]
        self.assertEqual(record["refusal_reason"], "readback_contract_mismatch")
        self.assertEqual(sorted(record["readback_mismatches"]), ["reconLevel", "scanningInterval"])
        self.assertEqual(ui.co_status(h.loaded_records())["code"], ui.CREATED_MISMATCH)

    def test_wrong_domain_name_or_uuid_is_a_mismatch(self):
        for row in (good_row(accountUuid="zz"), good_row(id="")):
            h = self.harness(readbacks=[reply([row])])
            self.assertEqual(h.go()["result"], "created_mismatch")
            self.assertEqual(h.page.confirm_clicks, 1)

    def test_row_never_appears_polling_is_bounded_and_unknown(self):
        h = self.harness(readbacks=[])
        self.assertEqual(h.go()["result"], "create_outcome_unknown")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, live.READBACK_ATTEMPTS))

    def test_row_appears_late_still_verifies_with_one_click(self):
        h = self.harness(readbacks=[reply([]), reply([]), reply([good_row()])])
        self.assertEqual(h.go()["result"], "created_verified")
        self.assertEqual((h.page.confirm_clicks, h.page.readback_calls), (1, 3))

    def test_confirm_that_cannot_be_used_sends_nothing_and_still_blocks_the_co(self):
        for kw in ({"confirm_enabled": False}, {"hit": False}):
            h = self.harness(**kw)
            result = h.go()
            self.assertEqual(result["result"], "confirm_not_clicked", kw)
            self.assertEqual(h.page.confirm_clicks, 0, kw)
            self.assertEqual(h.approval_unused(), "approval_consumed")
            self.assertEqual(h.outcomes().count("attempt_started"), 1)
            self.assertTrue(ui.co_status(h.loaded_records())["blocking"])
            self.assertFalse(h.page.form_open, "the half-filled form is cancelled")

    def test_the_attempt_is_recorded_before_the_click_and_the_form_values_are_the_production_plan(self):
        h = self.harness()
        h.go()
        started = [r for r in h.loaded_records() if r["outcome"] == "attempt_started"][0]
        self.assertTrue(all(v == "pass" for v in started["form_fields_checked"].values()))
        self.assertEqual(h.page.texts["Company name"].value, NAME)
        self.assertEqual(h.page.texts["Company primary domain"].value, DOMAIN)
        self.assertEqual(h.page.texts["User email domains (Comma Separated Values)"].value, "pentera.io")
        self.assertEqual(h.page.selects["accountType"].value, "Customer")
        self.assertEqual(h.page.selects["scanningInterval"].value, "None")
        self.assertEqual(h.page.selects["leakedCredentialsScanningInterval"].value, "Weekly")
        self.assertEqual(h.page.texts["Number of assets"].value, "1")

    def test_no_record_contains_a_secret_shaped_value(self):
        h = self.harness()
        h.go()
        for path in pcr.list_records(h.records, CO):
            text = Path(path).read_text(encoding="utf-8")
            for fragment in ("Bearer", "password", "cookie", "eyJ"):
                self.assertNotIn(fragment, text)


class GlueHygieneTests(unittest.TestCase):
    def test_account_add_appears_in_the_live_glue_only_through_the_live_module(self):
        text = Path(runner.__file__).read_text(encoding="utf-8")
        start = text.index("# --- Production CE-only LIVE CREATE")
        glue = text[start:text.index("def _autopublish_vm_snapshot")]
        self.assertNotIn("account/add", glue)
        self.assertNotIn('name="Confirm"', glue)

    def test_cli_flag_dispatch_and_exclusivity(self):
        with mock.patch.object(runner, "run_production_create", return_value='{"result":"x"}') as ran, \
                mock.patch.object(runner.sys, "argv", ["r", "--production-create", "--co", CO, "--revision", REV, "--owner-go"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
        ran.assert_called_once_with(CO, REV, owner_go=True)
        for extra in ([], ["--co", CO], ["--co", CO, "--revision", REV, "--dry-run"],
                      ["--co", CO, "--revision", REV, "--production-dry-run"],
                      ["--co", CO, "--revision", REV, "--route", "case_1_new_surface_only"]):
            with mock.patch.object(runner.sys, "argv", ["r", "--production-create", *extra]), \
                    mock.patch.object(runner, "run_production_create") as ran, self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                runner.main()
            ran.assert_not_called()
        with mock.patch.object(runner.sys, "argv", ["r", "--owner-go", "--co", CO]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main()


if __name__ == "__main__":
    unittest.main()
