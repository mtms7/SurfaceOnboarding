"""Renewals run from the dashboard (owner decision 2026-10-07, reverses "renewals CLI-only").

The orchestrated runner mode (mirror if needed, dry run, apply; no SpyCloud step, owner 2026-10-08), the runner-state bookkeeping, and the
dashboard's Start renewal route with its guards. Local only: every browser-facing function is stubbed and every state
file is a temporary one; nothing reaches Salesforce, Leonardo, Redash, or the network.
"""
from __future__ import annotations

import contextlib
import json
from datetime import date
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

REF = "CO-0767"
REV = "2026-10-07T08:00:00Z"
ENGINE = "case_6_renew_both"
REAL_LAUNCHER = dashboard.start_attended_renewal_runner
RENEWAL_ROW = {"Name": REF, "Onboarding_Product__c": "Surface & Credential Exposure",
               "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved",
               "LastModifiedDate": REV, "Account__c": "001000000000DEMO"}


class RunnerSandbox(unittest.TestCase):
    """Every runner state file in a temporary folder; the orchestrator's collaborators recorded and stubbed."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("RUNNER_STATE_PATH", "RUN_LOG_PATH", "READBACK_PATH", "MIRROR_PATH", "RENEWAL_OUTCOMES_PATH",
                     "SPYCLOUD_STATE_PATH", "DIAGNOSTICS_PATH", "CHECK_STATE_PATH"):
            self.stack.enter_context(patch.object(runner, name, self.dir / (name.lower() + ".json")))
        self.calls: list[tuple] = []
        self.source = SimpleNamespace(source_revision=REV, engine=ENGINE)

    def stub(self, *, mirror_verified=True, mirror="mirror_created_verified", dry=("renewal_dry_run_planned", {}),
             apply=("renewal_edit_verified", {}), source=None):
        calls = self.calls
        enter = self.stack.enter_context
        if isinstance(source, Exception):
            enter(patch.object(runner, "renewal_fill_source", side_effect=source))
        else:
            enter(patch.object(runner, "renewal_fill_source", return_value=source or self.source))
        enter(patch.object(runner, "renewal_mirror_verified", return_value=mirror_verified))

        def run_mirror(reference, confirm_write=False, env_name="dev"):
            calls.append(("mirror", reference, confirm_write))
            return mirror

        def run_renewal(reference, **kwargs):
            confirm = kwargs.get("confirm_write", False)
            calls.append(("apply" if confirm else "dry", reference, kwargs.get("expected_revision")))
            return apply if confirm else dry

        def run_spy(reference, **kwargs):
            calls.append(("spycloud", reference, kwargs.get("confirm_write")))
            raise AssertionError("a renewal run must never touch SpyCloud")

        enter(patch.object(runner, "run_renewal_mirror", side_effect=run_mirror))
        enter(patch.object(runner, "run_renewal", side_effect=run_renewal))
        enter(patch.object(runner, "run_spycloud_off", side_effect=run_spy))
        enter(patch.object(runner, "record_renewal_outcome",
                           side_effect=lambda ref, result, confirm, report: calls.append(("outcome", result, confirm))))

    def steps(self):
        return [call[0] for call in self.calls]

    def start_record(self, revision=REV):
        runner.record_runner_start(REF, revision, "2026-10-07T09:00:00", route=ENGINE)

    def record(self):
        return runner.load_runner_state()[REF]

    def log_events(self):
        run = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]
        return run, [(event["step"], event["outcome"]) for event in run["events"]]


class OrchestratorTests(RunnerSandbox):
    def test_no_mirror_then_mirror_dry_apply_verified(self):
        self.stub(mirror_verified=False)
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_edit_verified")
        self.assertEqual(self.steps(), ["mirror", "dry", "outcome", "apply", "outcome"])
        self.assertEqual(self.calls[0], ("mirror", REF, True))
        self.assertEqual((self.calls[1][2], self.calls[3][2]), (REV, REV))  # both pass the acknowledged revision
        self.assertEqual(self.record()["result"], "renewal_edit_verified")
        self.assertNotIn("uncertain", self.record())
        run, events = self.log_events()
        self.assertEqual(run["mode"], "renewal_run")
        self.assertEqual(events, [("source", "ok"), ("mirror", "mirror_created_verified"),
                                  ("renewal_dry_run", "renewal_dry_run_planned"),
                                  ("renewal_apply", "renewal_edit_verified"),
                                  ("finish", "renewal_edit_verified")])

    def window_source(self, apply_from):
        term = {"apply_from": apply_from}
        return SimpleNamespace(source_revision=REV, engine=ENGINE, surface_term=term, ce_term=term)

    def run_steps(self, env_name, source):
        log = runner.RunLog(REF, runner.RENEWAL_RUN_MODE, route="renewal_run")
        self.stub(mirror_verified=False, source=source)
        return runner._renewal_run_steps(REF, REV, log, env_name), log

    def test_production_before_apply_from_stops_before_the_mirror_and_any_write(self):
        for env in ("prod", "production", "staging", ""):
            with self.subTest(env=env):
                self.calls.clear()
                with contextlib.ExitStack() as stack, patch.object(runner, "_run_day", return_value=date(2026, 10, 6)):
                    self.stack = stack
                    outcome, log = self.run_steps(env, self.window_source("2026-10-20"))
                self.assertEqual(outcome, ("renewal_not_yet_applicable", ""))
                self.assertEqual(self.steps(), [])  # no mirror, no dry run, no apply
                self.assertIn(("apply_window", "renewal_not_yet_applicable"), [(e["step"], e["outcome"]) for e in log.events])

    def test_dev_is_exempt_and_production_on_or_after_apply_from_proceeds(self):
        with patch.object(runner, "_run_day", return_value=date(2026, 10, 6)):
            outcome, _log = self.run_steps("dev", self.window_source("2026-10-20"))
            self.assertEqual(outcome, ("renewal_edit_verified", ""))
            self.assertEqual(self.steps(), ["mirror", "dry", "outcome", "apply", "outcome"])
            self.calls.clear()
            with contextlib.ExitStack() as stack:
                self.stack = stack
                outcome, _log = self.run_steps("prod", self.window_source("2026-10-06"))
            self.assertEqual(outcome, ("renewal_edit_verified", ""))

    def test_production_with_an_unreadable_apply_from_fails_closed(self):
        outcome, _log = self.run_steps("prod", self.source)  # the plain test source carries no terms
        self.assertEqual(outcome, ("renewal_not_yet_applicable", ""))
        self.assertEqual(self.steps(), [])

    def test_existing_verified_mirror_is_skipped(self):
        self.stub(mirror_verified=True)
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_edit_verified")
        self.assertNotIn("mirror", self.steps())
        self.assertIn(("mirror", "skipped_verified_mirror"), self.log_events()[1])

    def test_a_maybe_created_mirror_stops_the_run_as_uncertain(self):
        for code in sorted(runner.MIRROR_AFTER_CONFIRM_RESULTS):
            with self.subTest(code=code):
                self.calls.clear()
                runner.RUNNER_STATE_PATH.unlink(missing_ok=True)
                with contextlib.ExitStack() as stack:
                    self.stack = stack
                    self.stub(mirror_verified=False, mirror=code)
                    self.start_record()
                    self.assertEqual(runner.run_renewal_onboarding(REF, REV), code)
                self.assertEqual(self.steps(), ["mirror"])  # no dry run, no apply
                self.assertEqual(self.record()["uncertain"], "mirror_create")
                self.assertTrue(runner.renewal_uncertain(self.record()))

    def test_a_mirror_stop_before_any_write_is_a_plain_failure(self):
        self.stub(mirror_verified=False, mirror="mirror_prod_tenant_not_found")
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "mirror_prod_tenant_not_found")
        self.assertEqual(self.steps(), ["mirror"])
        self.assertNotIn("uncertain", self.record())
        self.assertFalse(runner.renewal_uncertain(self.record()))

    def test_already_current_finishes_ok_without_applying(self):
        self.stub(dry=("renewal_already_current", {}))
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_already_current")
        self.assertEqual(self.steps(), ["dry", "outcome"])
        self.assertEqual(self.record()["result"], "renewal_already_current")

    def test_any_other_dry_run_code_stops_before_apply(self):
        # renewal_expiration_would_shorten is no longer produced (owner 2026-10-07: an already-renewed expiry is kept);
        # renewal_not_yet_applicable (production apply window) takes its place in this list of stop codes.
        for code in ("renewal_domains_mismatch_manual_review", "renewal_not_yet_applicable",
                     "renewal_new_domain_in_production", "renewal_target_not_in_production_clone",
                     "renewal_target_ambiguous_in_production_clone", "production_clone_unavailable",
                     "production_clone_incomplete", "renewal_form_mismatch", "source_revision_drift"):
            with self.subTest(code=code):
                self.calls.clear()
                runner.RUNNER_STATE_PATH.unlink(missing_ok=True)
                with contextlib.ExitStack() as stack:
                    self.stack = stack
                    self.stub(dry=(code, {}))
                    self.start_record()
                    self.assertEqual(runner.run_renewal_onboarding(REF, REV), code)
                self.assertNotIn("apply", self.steps())
                self.assertNotIn("spycloud", self.steps())
                self.assertEqual(self.record()["result"], code)
                self.assertNotIn("uncertain", self.record())

    def test_apply_unverified_is_uncertain_and_blocks_a_second_start(self):
        for code in ("renewal_save_no_signal", "renewal_saved_unverified", "renewal_readback_changed_mismatch"):
            with self.subTest(code=code):
                self.calls.clear()
                runner.RUNNER_STATE_PATH.unlink(missing_ok=True)
                with contextlib.ExitStack() as stack:
                    self.stack = stack
                    self.stub(apply=(code, {}))
                    self.start_record()
                    self.assertEqual(runner.run_renewal_onboarding(REF, REV), code)
                self.assertNotIn("spycloud", self.steps())
                self.assertEqual(self.record()["uncertain"], "renewal_write")
                self.assertEqual(runner.start_blocker(self.record()), "renewal_uncertain")
                with self.assertRaises(ValueError):
                    runner.reset_runner_record(REF)
                with self.assertRaises(ValueError):
                    runner.record_runner_start(REF, "newer-revision", "2026-10-07T10:00:00")

    def test_apply_that_stopped_before_confirm_is_a_plain_failure(self):
        self.stub(apply=("renewal_confirm_disabled", {}))
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_confirm_disabled")
        self.assertNotIn("uncertain", self.record())
        self.assertTrue(runner.reset_runner_record(REF))  # nothing pending: Reset re-arms it

    def test_a_renewal_never_touches_spycloud_and_the_result_is_unchanged(self):
        # Owner 2026-10-08: Leonardo's default (ON) stays after a renewal; no automatic write, no SpyCloud log event.
        self.stub()
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_edit_verified")
        self.assertNotIn("spycloud", self.steps())  # run_spycloud_off (stubbed to raise) was never called
        events = self.log_events()[1]
        self.assertFalse([step for step, _outcome in events if step.startswith("spycloud")])
        self.assertEqual(self.record()["result"], "renewal_edit_verified")
        self.assertNotIn("uncertain", self.record())

    def test_source_failures_stop_before_any_browser_step(self):
        self.stub(source=runner.SurfaceSourceError("renewal_not_approved"))
        self.start_record()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_not_approved")
        self.assertEqual(self.calls, [])

    def test_a_revision_that_moved_stops_the_run(self):
        self.stub()
        self.start_record("an-older-revision")
        self.assertEqual(runner.run_renewal_onboarding(REF, "an-older-revision"), "source_revision_drift")
        self.assertEqual(self.calls, [])

    def test_production_environment_is_refused_before_anything(self):
        self.stub()
        self.assertEqual(runner.run_renewal_onboarding(REF, REV, env_name="prod"), "renewal_environment_not_supported")
        self.assertEqual(self.calls, [])

    def test_unexpected_errors_never_leave_the_record_running(self):
        self.stub()
        with patch.object(runner, "run_renewal", side_effect=ValueError("boom")):
            self.start_record()
            self.assertEqual(runner.run_renewal_onboarding(REF, REV), "runner_crashed")
        self.assertEqual(self.record()["result"], "runner_crashed")

    def test_the_run_log_holds_codes_only(self):
        self.stub(mirror_verified=False)
        self.start_record()
        runner.run_renewal_onboarding(REF, REV)
        text = runner.RUN_LOG_PATH.read_text(encoding="utf-8")
        run, _events = self.log_events()
        self.assertEqual({event["detail"] for event in run["events"] if "detail" in event}, {ENGINE})  # the engine code only
        for forbidden in ("http", "@", "token", "password"):
            self.assertNotIn(forbidden, text)


class RunnerStateTests(RunnerSandbox):
    def test_renewal_route_and_uncertain_marker_round_trip(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        record = self.record()
        self.assertEqual((record["route"], record["uncertain"]), (ENGINE, "renewal_write"))

    def test_invalid_markers_fail_closed(self):
        runner.RUNNER_STATE_PATH.write_text(json.dumps({REF: {"source_revision": REV, "uncertain": "bogus"}}), encoding="utf-8")
        with self.assertRaises(runner.RunnerStateUnavailable):
            runner.load_runner_state()

    def test_a_dry_run_settles_an_uncertain_apply(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        self.assertIsNone(runner.settle_uncertain_renewal(REF, "renewal_dry_run_failed", "2026-10-07T09:20:00"))
        self.assertTrue(runner.renewal_uncertain(self.record()))
        self.assertEqual(runner.settle_uncertain_renewal(REF, "renewal_already_current", "2026-10-07T09:20:00"), "applied")
        self.assertEqual(self.record()["result"], "renewal_edit_verified")
        self.assertFalse(runner.renewal_uncertain(self.record()))

    def test_a_dry_run_that_still_plans_the_change_means_not_applied(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_failed", "2026-10-07T09:10:00", uncertain="renewal_write")
        self.assertEqual(runner.settle_uncertain_renewal(REF, "renewal_dry_run_planned", "2026-10-07T09:20:00"), "not_applied")
        self.assertFalse(runner.renewal_uncertain(self.record()))
        self.assertIsNone(runner.start_blocker(self.record()))
        self.assertTrue(runner.reset_runner_record(REF))

    def test_a_mirror_uncertain_run_is_never_settled_by_a_dry_run(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "mirror_create_unverified", "2026-10-07T09:10:00", uncertain="mirror_create")
        self.assertIsNone(runner.settle_uncertain_renewal(REF, "renewal_already_current", "2026-10-07T09:20:00"))
        self.assertTrue(runner.renewal_uncertain(self.record()))

    def test_only_one_run_can_be_open_per_co(self):
        self.start_record()
        with self.assertRaises(ValueError) as caught:
            runner.record_runner_start(REF, "another", "2026-10-07T09:01:00", route=ENGINE)
        self.assertEqual(str(caught.exception), "run_in_progress")


class CliTests(RunnerSandbox):
    def run_main(self, *argv):
        with patch("sys.argv", ["attended_ce_only_playwright.py", *argv]), patch("builtins.print") as printed:
            code = runner.main()
        return code, [call.args[0] for call in printed.call_args_list]

    def test_renew_run_calls_the_orchestrator_with_the_revision(self):
        with patch.object(runner, "run_renewal_onboarding", return_value="renewal_edit_verified") as run:
            code, output = self.run_main("--co", REF, "--revision", REV, "--renew-run")
        run.assert_called_once_with(REF, REV, env_name="dev")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0]), {"result": "renewal_edit_verified", "salesforce_writeback": "not_performed"})

    def test_renew_run_needs_a_revision_and_never_combines(self):
        for argv in (["--co", REF, "--renew-run"], ["--co", REF, "--revision", REV, "--renew-run", "--confirm-write"],
                     ["--co", REF, "--revision", REV, "--renew-run", "--renew"]):
            with self.subTest(argv=argv), patch.object(runner, "run_renewal_onboarding", side_effect=AssertionError("no run")):
                with self.assertRaises(SystemExit):
                    self.run_main(*argv)

    def test_the_diagnostic_flags_still_work(self):
        with patch.object(runner, "run_renewal", return_value=("renewal_dry_run_planned", {})) as renew, \
                patch.object(runner, "record_renewal_outcome"):
            code, output = self.run_main("--co", REF, "--renew")
        self.assertEqual(code, 0)
        renew.assert_called_once()
        with patch.object(runner, "run_renewal_mirror", return_value="mirror_dry_run_verified") as mirror:
            self.run_main("--co", REF, "--mirror-renewal")
        mirror.assert_called_once()

    def test_a_cli_dry_run_settles_an_uncertain_apply(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        with patch.object(runner, "run_renewal", return_value=("renewal_already_current", {})), \
                patch.object(runner, "record_renewal_outcome"):
            self.run_main("--co", REF, "--renew")
        self.assertEqual(self.record()["result"], "renewal_edit_verified")


class ReadbackCaptureTests(unittest.TestCase):
    """Both IDs are required for every route's readback; the dashboard shows what the route needs."""

    def test_readback_evidence_requires_both_ids_for_every_route(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, "READBACK_PATH", Path(folder) / "rb.json"):
            for bad in (("", "a" * 32), ("A" * 20, ""), ("A" * 20, "short")):
                with self.assertRaises(ValueError):
                    runner.write_readback_evidence("CO-0702", bad[0], bad[1], "2026-10-07")
            runner.write_readback_evidence("CO-0702", "A" * 20, "a" * 32, "2026-10-07")
            stored = json.loads((Path(folder) / "rb.json").read_text(encoding="utf-8"))["CO-0702"]
            self.assertEqual((stored["surface_account_id"], stored["account_uuid"]), ("A" * 20, "a" * 32))

    def test_the_search_readback_needs_both_ids_whatever_the_route(self):
        row = {"accountName": "Acme - CE Only", "id": "A" * 24, "accountUuid": "a" * 32, "lastReconScan": None}
        found = runner._api_readback(SimpleNamespace(rows=[row]), "Acme - CE Only", None)
        self.assertEqual(found, ("A" * 24, "a" * 32, "No scan started"))
        for broken in ({**row, "accountUuid": None}, {**row, "id": None}, {**row, "accountUuid": "short"}):
            self.assertIsNone(runner._api_readback(SimpleNamespace(rows=[broken]), "Acme - CE Only", None))


class FakeRequest:
    """Stands in for the HTTP handler: records pages and redirects; reuses the real Handler methods."""

    def __init__(self, path):
        self.path, self.pages, self.redirects, self.headers = path, [], [], {}

    def send_page(self, status, page):
        self.pages.append((int(status), page))

    def send_redirect(self, location):
        self.redirects.append(location)

    def _start_renewal(self, reference, form, row):
        return dashboard.Handler._start_renewal(self, reference, form, row)

    def _claim_and_launch(self, reference, revision, launch, **fields):
        return dashboard.Handler._claim_and_launch(self, reference, revision, launch, **fields)


class StartRenewalRouteTests(RunnerSandbox):
    def setUp(self):
        super().setUp()
        enter = self.stack.enter_context
        enter(patch.object(dashboard, "login_required", return_value=False))
        enter(patch.object(dashboard, "action_readiness_problem", return_value=None))
        self.launched: list[tuple] = []
        enter(patch.object(dashboard, "start_attended_renewal_runner",
                           side_effect=lambda ref, rev: self.launched.append((ref, rev)) or True))
        enter(patch.object(dashboard, "detail_row", side_effect=lambda ref: dict(self.row)))
        self.row = dict(RENEWAL_ROW)

    def form(self, **overrides):
        nonce = dashboard.renewal_start_ack_nonce(REF, self.row)
        values = {"reference": REF, "source_revision": REV, "nonce": nonce, "plan_reviewed": "1",
                  "attended_renewal_authorized": "1", **overrides}
        return {key: [value] for key, value in values.items() if value is not None}

    def post(self, form=None):
        request = FakeRequest("/attended/start-renewal")
        with patch.object(dashboard, "post_form", return_value=self.form() if form is None else form):
            dashboard.Handler.do_POST(request)
        return request

    def assert_refused(self, request, text=None):
        self.assertEqual(self.launched, [])
        self.assertEqual(request.redirects, [])
        self.assertEqual(request.pages[0][0], 409)
        if text:
            self.assertIn(text, request.pages[0][1])

    def test_a_valid_start_claims_the_run_and_launches_the_renewal_runner(self):
        request = self.post()
        self.assertEqual(self.launched, [(REF, REV)])
        self.assertEqual(request.redirects, ["/attended/ce-only-runner-status?ref=" + REF])
        record = self.record()
        self.assertEqual((record["source_revision"], record["route"]), (REV, ENGINE))
        self.assertNotIn("result", record)  # running until the runner reports

    def test_the_launch_command_is_dev_only_and_has_no_write_flags(self):
        calls = []
        with patch.object(dashboard.subprocess, "Popen", side_effect=lambda argv, **kw: calls.append(argv)),                 patch.object(dashboard, "local_browser_launch_allowed", return_value=True):
            self.assertTrue(REAL_LAUNCHER(REF, REV))
            self.assertFalse(REAL_LAUNCHER("not-a-co", REV))
            self.assertFalse(REAL_LAUNCHER(REF, ""))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][2:], ["--co", REF, "--revision", REV, "--renew-run"])
        for forbidden in ("--env", "prod", "--confirm-write"):
            self.assertNotIn(forbidden, calls[0])

    def test_both_attestations_are_required(self):
        for missing in ("plan_reviewed", "attended_renewal_authorized"):
            with self.subTest(missing=missing):
                self.assert_refused(self.post(self.form(**{missing: None})), "confirmations are required")

    def test_the_nonce_is_required_and_one_time(self):
        self.assert_refused(self.post(self.form(nonce="forged")), "expired")
        self.launched.clear()
        form = self.form()
        self.assertEqual(self.post(form).redirects, ["/attended/ce-only-runner-status?ref=" + REF])
        self.launched.clear()
        runner.RUNNER_STATE_PATH.unlink()
        self.assert_refused(self.post(form), "expired")  # the same nonce cannot start a second run
        self.launched.clear()
        self.assert_refused(self.post(self.form(nonce=None)), "expired")

    def test_the_source_revision_must_match(self):
        self.assert_refused(self.post(self.form(source_revision="2026-10-07T07:00:00Z")), "source revision changed")
        self.assert_refused(self.post(self.form(source_revision=None)), "acknowledgement is missing")

    def test_a_revision_that_moved_after_the_nonce_was_issued_is_refused(self):
        form = self.form()
        self.row["LastModifiedDate"] = "2026-10-07T08:30:00Z"
        self.assert_refused(self.post(form), "source revision changed")

    def test_approval_must_be_approved(self):
        self.row["Onboarding_Approval_Status__c"] = "Pending"
        self.assert_refused(self.post(), "not Approved")

    def test_single_product_renewals_and_new_onboardings_have_no_start(self):
        for product, kind in (("Surface", "Renewal of Existing Product"), ("Credential Exposure", "Renewal of Existing Product"),
                              ("Surface & Credential Exposure", "New Product Onboarding")):
            with self.subTest(product=product, kind=kind):
                self.row.update({"Onboarding_Product__c": product, "Onboarding_Type__c": kind})
                self.assert_refused(self.post(), "not a Case 4-6 renewal")

    def test_a_run_in_progress_for_this_co_or_another_blocks_the_start(self):
        self.start_record("older")
        self.assert_refused(self.post(), "has not reported a result")
        runner.RUNNER_STATE_PATH.unlink()
        runner.record_runner_start("CO-0999", "x", "2026-10-07T09:00:00")
        self.assert_refused(self.post(), "one run at a time")
        self.assertNotIn(REF, runner.load_runner_state())

    def test_an_uncertain_run_blocks_the_start(self):
        self.start_record("older")
        runner.record_runner_result(REF, "older", "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        self.assert_refused(self.post(), "may have written to Leonardo Development")

    def test_the_same_revision_that_already_ran_shows_its_status_instead(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_domains_mismatch_manual_review", "2026-10-07T09:10:00")
        request = self.post()
        self.assertEqual(self.launched, [])
        self.assertEqual(request.redirects, ["/attended/ce-only-runner-status?ref=" + REF])

    def test_an_unreadable_runner_state_fails_closed(self):
        runner.RUNNER_STATE_PATH.write_text("not json", encoding="utf-8")
        request = self.post()
        self.assertEqual(self.launched, [])
        self.assertEqual(request.pages[0][0], 503)

    def test_a_failed_launch_is_recorded_and_reported(self):
        with patch.object(dashboard, "start_attended_renewal_runner", return_value=False):
            request = self.post()
        self.assertEqual(request.pages[0][0], 409)
        self.assertEqual(self.record()["result"], "runner_launch_failed")

    def test_the_route_is_post_only_and_gated_like_a_start(self):
        self.assertIn("/attended/start-renewal", dashboard.POST_ROUTES)
        self.assertEqual(dashboard.SESSION_GATED_ROUTES["/attended/start-renewal"], "start")
        request = FakeRequest("/attended/start-renewal")
        with patch.object(dashboard, "action_readiness_problem", return_value="leonardo_expired"), \
                patch.object(dashboard, "post_form", return_value=self.form()), \
                patch.object(dashboard, "session_statuses", return_value={
                    "salesforce": dashboard.SessionStatus(dashboard.SessionState.READY, "ok", dashboard.readiness_now()),
                    "leonardo": dashboard.SessionStatus(dashboard.SessionState.EXPIRED, "leonardo_session_expired",
                                                        dashboard.readiness_now())}):
            dashboard.Handler.do_POST(request)
        self.assertEqual(self.launched, [])
        self.assertEqual(request.pages[0][0], 409)

    def test_origin_and_login_guards_apply_to_the_new_route(self):
        # The shared guards run before any handler: foreign origins and signed-out requests never reach it.
        self.assertEqual(dashboard.request_origin_problem("POST", "127.0.0.1:8012", "http://evil.example", None, 8012),
                         "origin_not_allowed")
        self.assertEqual(dashboard.request_origin_problem("POST", "127.0.0.1:8012", None, "cross-site", 8012), "cross_site_post")
        self.assertIsNone(dashboard.request_origin_problem("POST", "127.0.0.1:8012", "http://127.0.0.1:8012", None, 8012))
        request = FakeRequest("/attended/start-renewal")
        with patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "dashboard_operator", return_value=None):
            dashboard.Handler.do_POST(request)
        self.assertEqual(request.pages[0][0], 403)
        self.assertEqual(self.launched, [])

    def test_the_renewal_runner_has_no_production_mode(self):
        import inspect
        source = inspect.getsource(runner.run_renewal_onboarding) + inspect.getsource(runner._renewal_run_steps)
        self.assertIn('env_name != "dev"', source)
        self.assertNotIn("confirm_write=env", source)
        self.assertNotIn('"prod"', source)


class NonceTests(unittest.TestCase):
    ROW = dict(RENEWAL_ROW)

    def setUp(self):
        dashboard._renewal_start_acks.clear()  # module state: earlier tests may have issued a nonce for this CO

    def test_nonce_is_reused_until_consumed_and_expires(self):
        first = dashboard.renewal_start_ack_nonce(REF, self.ROW, now=100.0)
        self.assertEqual(dashboard.renewal_start_ack_nonce(REF, self.ROW, now=150.0), first)
        self.assertFalse(dashboard.consume_renewal_start_ack(REF, self.ROW, first, now=100.0 + 16 * 60))
        again = dashboard.renewal_start_ack_nonce(REF, self.ROW, now=2000.0)
        self.assertNotEqual(again, first)
        self.assertTrue(dashboard.consume_renewal_start_ack(REF, self.ROW, again, now=2001.0))
        self.assertFalse(dashboard.consume_renewal_start_ack(REF, self.ROW, again, now=2002.0))

    def test_no_nonce_unless_approved_with_a_revision(self):
        self.assertIsNone(dashboard.renewal_start_ack_nonce(REF, dict(self.ROW, Onboarding_Approval_Status__c="Pending")))
        self.assertIsNone(dashboard.renewal_start_ack_nonce(REF, {k: v for k, v in self.ROW.items() if k != "LastModifiedDate"}))


class ResultTextTests(unittest.TestCase):
    def test_every_renewal_and_mirror_code_has_plain_language(self):
        codes = set(runner.RENEWAL_AFTER_CONFIRM_RESULTS | runner.MIRROR_AFTER_CONFIRM_RESULTS)
        codes |= {"renewal_edit_verified", "renewal_already_current", "renewal_domains_mismatch_manual_review",
                  "renewal_expiration_would_shorten", "renewal_not_yet_applicable",
                  "renewal_new_domain_in_production", "renewal_not_onboarded",
                  "renewal_not_approved", "renewal_route_not_supported", "renewal_ce_email_domain_invalid",
                  "mirror_already_exists", "mirror_readback_exists", "mirror_clone_unavailable", "mirror_prod_tenant_not_found",
                  "mirror_prod_tenant_ambiguous", "mirror_duplicate_found", "source_revision_drift"}
        for code in sorted(codes - {"leonardo_session_expired"}):
            with self.subTest(code=code):
                kind, message = dashboard.runner_result_message(code)
                self.assertNotIn("The runner finished with result code", message)
                self.assertIn(kind, ("success", "blocked", "info"))
        self.assertEqual(dashboard.runner_result_message("renewal_edit_verified")[0], "success")
        self.assertEqual(dashboard.runner_result_message("renewal_already_current")[0], "success")
        self.assertEqual(dashboard.runner_result_message("renewal_saved_unverified")[0], "info")

    def test_apply_window_and_kept_expiry_texts(self):
        self.assertEqual(dashboard.runner_result_message("renewal_not_yet_applicable")[0], "blocked")
        self.assertIn("14 days", dashboard.runner_result_message("renewal_not_yet_applicable")[1])
        self.assertIn("renewal_not_yet_applicable", dashboard.RENEWAL_RESULT_TEXT)
        self.assertIn("renewal_not_yet_applicable", dashboard.RENEWAL_CLI_BLOCK_TEXT)
        self.assertIn("renewal_not_yet_applicable", dashboard.RENEWAL_RUN_HEADLINES)
        text = dashboard.renewal_apply_text({"applicable_now": False, "apply_from": "2026-10-02"})
        self.assertEqual(text, "Apply from 2026-10-02 — enforced in production; the Dev mirror may be renewed early")
        self.assertIn("enforced in production", dashboard.renewal_apply_text({"applicable_now": True, "apply_from": "x"}))
        self.assertEqual(dashboard.renewal_apply_text(None), "—")
        self.assertEqual(dashboard.renewal_expiry_text("2030-01-01", "2029-10-15"),
                         "Expiry kept (already 2030-01-01, DealHub term end 2029-10-15)")
        self.assertEqual(dashboard.renewal_expiry_text("2029-10-15", "2029-10-15"),
                         "Expiry kept (already 2029-10-15, DealHub term end 2029-10-15)")
        self.assertEqual(dashboard.renewal_expiry_text("2026-10-15", "2029-10-15"),
                         "2026-10-15 → 2029-10-15 (DealHub term end)")
        self.assertEqual(dashboard.renewal_expiry_text("read from the mirror", "2029-10-15"),
                         "read from the mirror → 2029-10-15 (DealHub term end)")

    def test_the_outcome_record_and_card_carry_the_kept_expiry_and_apply_from(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "outcomes.json"
        report = {"engine": ENGINE, "changes": [], "added_domains": 0, "expiration": "kept_already_current_or_later",
                  "expiration_current": "2030-01-01", "expiration_target": "2029-10-15", "apply_from": "2026-10-02"}
        with patch.object(runner, "RENEWAL_OUTCOMES_PATH", path), patch.object(dashboard, "RENEWAL_OUTCOMES_PATH", path):
            runner.write_renewal_outcome(REF, "renewal_already_current", True, "not_performed",
                                         runner.datetime(2026, 10, 7, 9, 0), None, report)
            stored = json.loads(path.read_text(encoding="utf-8"))[REF]
            self.assertEqual((stored["expiration_kept_current"], stored["expiration_kept_target"], stored["apply_from"]),
                             ("2030-01-01", "2029-10-15", "2026-10-02"))
            card = dashboard._renewal_outcome_section(REF)
            self.assertIn("Expiry kept (already 2030-01-01, DealHub term end 2029-10-15)", card)
            runner.write_renewal_outcome(REF, "renewal_not_yet_applicable", True, "not_performed",
                                         runner.datetime(2026, 10, 7, 9, 0), None, {**report, "expiration": "planned"})
            self.assertNotIn("expiration_kept_current", json.loads(path.read_text(encoding="utf-8"))[REF])
            card = dashboard._renewal_outcome_section(REF)
            self.assertIn("Apply from", card)
            self.assertIn("2026-10-02", card)

    def test_blocked_plan_codes_and_unknown_codes(self):
        self.assertIn("No Core Plus term", dashboard.runner_result_message("renewal_blocked_no_core_plus_term")[1])
        self.assertIn("result code <code>weird_code</code>", dashboard.runner_result_message("weird_code")[1])
        self.assertIn("&lt;b&gt;", dashboard.runner_result_message("<b>")[1])

    def test_queue_and_chip_know_renewal_runs(self):
        row = {"Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "Request Approved",
               "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "Renewal of Existing Product"}
        base = {"source_revision": REV, "route": ENGINE}
        queue = lambda record: dashboard.classify_queue_row(row, record)[0]  # noqa: E731
        self.assertEqual(queue(base), "ready")
        self.assertEqual(queue({**base, "result": "renewal_edit_verified"}), "scanning")
        self.assertEqual(queue({**base, "result": "renewal_domains_mismatch_manual_review"}), "review")
        self.assertEqual(queue({**base, "result": "renewal_save_no_signal", "uncertain": "renewal_write"}), "review")
        self.assertIn("Renewed (Dev)", dashboard._run_chip({**base, "result": "renewal_edit_verified"}))
        self.assertIn("Renewal uncertain", dashboard._run_chip({**base, "result": "renewal_save_no_signal", "uncertain": "renewal_write"}))
        self.assertIn("Renewal stopped", dashboard._run_chip({**base, "result": "renewal_form_mismatch"}))

    def test_status_page_shows_the_renewal_wording_and_verify_button(self):
        running = dashboard.page_ce_only_runner_status({REF: {"source_revision": REV, "route": ENGINE,
                                                              "started_on": "2026-10-07T09:00:00"}}, REF)
        self.assertIn("Renewal in progress", running)
        uncertain = dashboard.page_ce_only_runner_status(
            {REF: {"source_revision": REV, "route": ENGINE, "result": "renewal_save_no_signal", "uncertain": "renewal_write",
                   "completed_on": "2026-10-07T09:10:00"}}, REF)
        self.assertIn("action='/attended/verify-uncertain'", uncertain)
        done = dashboard.page_ce_only_runner_status(
            {REF: {"source_revision": REV, "route": ENGINE, "result": "renewal_edit_verified",
                   "completed_on": "2026-10-07T09:10:00"}}, REF)
        self.assertIn("Renewal applied (Dev)", done)


class VerifyUncertainRouteTests(RunnerSandbox):
    def setUp(self):
        super().setUp()
        enter = self.stack.enter_context
        enter(patch.object(dashboard, "login_required", return_value=False))
        enter(patch.object(dashboard, "action_readiness_problem", return_value=None))
        enter(patch.object(dashboard, "detail_row", side_effect=lambda ref: dict(RENEWAL_ROW)))
        self.modes: list[tuple] = []
        enter(patch.object(dashboard, "_start_runner_mode", side_effect=lambda *a: self.modes.append(a) or True))

    def post(self):
        request = FakeRequest("/attended/verify-uncertain")
        with patch.object(dashboard, "post_form", return_value={"reference": [REF]}):
            dashboard.Handler.do_POST(request)
        return request

    def test_an_uncertain_apply_is_verified_by_the_read_only_dry_run(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        request = self.post()
        self.assertEqual(self.modes, [("--co", REF, "--renew")])  # never --confirm-write
        self.assertEqual(request.redirects, ["/co/" + REF])

    def test_an_uncertain_mirror_cannot_be_verified_from_the_dashboard(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "mirror_create_unverified", "2026-10-07T09:10:00", uncertain="mirror_create")
        request = self.post()
        self.assertEqual(self.modes, [])
        self.assertEqual(request.pages[0][0], 409)

    def test_reset_of_an_uncertain_renewal_is_refused(self):
        self.start_record()
        runner.record_runner_result(REF, REV, "renewal_save_no_signal", "2026-10-07T09:10:00", uncertain="renewal_write")
        request = FakeRequest("/attended/reset-ce-only-runner")
        with patch.object(dashboard, "post_form", return_value={"reference": [REF], "reset_authorized": ["1"]}):
            dashboard.Handler.do_POST(request)
        self.assertEqual(request.pages[0][0], 409)
        self.assertIn("may have written to Leonardo Development", request.pages[0][1])
        self.assertIn("uncertain", self.record())


if __name__ == "__main__":
    unittest.main()
