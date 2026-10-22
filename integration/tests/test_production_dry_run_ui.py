"""Dashboard side of the production DRY RUN for Case 3 (CO-0800; owner decision 2026-10-20: a launch button with a progress bar).

* The card and the POST are keyed on PRODUCTION_DRY_RUN_SUPPORTED only (the runner constant stays False), desktop only, owner only.
* Every gate is re-judged at the POST; one-time token, an explicit authorization box, one launch, no retry.
* A Case 3 CO never gets a create control: the create gate and card stay CE-only.
* The progress bar is built only from recorded milestones and the recorded result; nothing is simulated.
Fakes only: nothing is started, read or written outside a temporary folder.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import production_dry_run_ui as ui
from integration.onboarding import run_progress
from integration.onboarding import session_readiness as readiness

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
CO = "CO-0800"
REF = "PROD-C3-0800-20261020"
REV = "2026-10-20T09:00:00.000+0000"
OWNER = "milton.stevenson@example.com"
CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
FORBIDDEN = ("<form", "<button", "<input", "method='post'")


def row(**over):
    data = {"Id": "a0B5g00000AbCdEFGH", "Name": CO, "LastModifiedDate": REV,
            "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
            "Onboarding_Approval_Status__c": "Approved", "Account_UUID__c": None, "Surface_Account_ID__c": None}
    data.update(over)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.approvals = self.dir / "approvals.json"
        approval.issue(self.approvals, REF, CO, CASE3, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1),
                       tenant_name="Test Tenant")
        # Explicit lock state (False/False) so these tests do not depend on the build's constants: ``unlocked()`` overrides it.
        for patch in (mock.patch.object(create_ui, "TOKENS", create_ui.OneTimeTokens()),
                      mock.patch.object(create_ui, "LAUNCHES", create_ui.LaunchRegistry()),
                      mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False),
                      mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False)):
            patch.start()
            self.addCleanup(patch.stop)

    def inputs(self, **over):
        data = dict(reference=CO, row=row(), now=NOW, environ={create_ui.SESSIONS_ENV: "1"}, operator=OWNER, vm=False,
                    approvals_path=self.approvals, records_parent=self.dir, clone={"state": "fresh", "age_seconds": 3600},
                    sf_ready=True, row_fresh=True)
        data.update(over)
        return create_ui.Inputs(**data)

    def unlocked(self, dry=True, runner=False):
        for patch in (mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry),
                      mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", runner)):
            patch.start()
            self.addCleanup(patch.stop)

    def record(self, outcome, when=NOW, **extra):
        data = {"co_reference": CO, "approval_ref": REF, "outcome": outcome, "route": CASE3, "salesforce_revision": REV, **extra}
        return pcr.record(self.dir, data, when)

    def nonce(self, html):
        return re.search(r"name='nonce' value='([^']+)'", html).group(1)


class GateTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()

    def test_the_dry_run_constant_alone_opens_the_gate_and_the_runner_constant_alone_does_not(self):
        self.assertIsNone(ui.gate(self.inputs())[0])
        self.assertTrue(create_ui.locked())  # the create stays locked: it needs BOTH constants
        with mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False), \
                mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            self.assertEqual(ui.gate(self.inputs())[0], "production_dry_run_not_supported")

    def breakers(self):
        other_ref = "PROD-C3-0800-20261019"
        return [
            ("vm_read_only", dict(vm=True)),
            ("salesforce_not_fresh", dict(row=None)),
            ("route_not_case3", dict(row=row(Onboarding_Product__c="Credential Exposure"))),
            ("approval_missing", dict(reference="CO-0899", row=row(Name="CO-0899"))),
            ("operator_not_owner", dict(operator="someone.else@example.com")),
            ("production_locked", dict(environ={})),
            ("approval_revision_drift", dict(row=row(LastModifiedDate="2026-10-20T10:00:00.000+0000"))),
            ("salesforce_not_approved", dict(row=row(Onboarding_Approval_Status__c="Pending"))),
            ("salesforce_not_fresh", dict(row_fresh=False)),
            ("salesforce_session_not_ready", dict(sf_ready=False)),
            ("salesforce_prior_uuid_present", dict(row=row(Account_UUID__c="a" * 32))),
            ("salesforce_prior_uuid_present", dict(row=row(Surface_Account_ID__c="b" * 24))),
            ("salesforce_prior_uuid_present", dict(row={k: v for k, v in row().items() if k != "Surface_Account_ID__c"})),
            ("clone_stale", dict(clone={"state": "stale", "age_seconds": 30000})),
            ("launch_in_progress", dict(launching=True)),
        ], other_ref

    def test_each_refusal_fires_alone(self):
        cases, _ = self.breakers()
        for code, over in cases:
            with self.subTest(code=code, over=list(over)):
                self.assertEqual(ui.gate(self.inputs(**over))[0], code)

    def test_a_case_3_dry_run_does_not_need_a_prior_dry_run_or_the_runner_constant(self):
        self.assertIsNone(ui.gate(self.inputs())[0])  # no records at all, runner constant False

    def test_only_a_case_3_approval_opens_it(self):
        for other in ("case_2_new_ce_only",):
            self.setUp()
            self.unlocked()
            self.approvals.unlink()
            approval.issue(self.approvals, "PROD-CE-0800-20261020", CO, other, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1),
                           tenant_name=("Test Tenant" if other == "case_3_combined_baseline" else None))
            self.assertEqual(ui.gate(self.inputs())[0], "approval_route_mismatch")

    def test_expired_and_consumed_approvals_are_named(self):
        self.assertEqual(ui.gate(self.inputs(now=NOW + timedelta(days=8)))[0], "approval_expired")
        approval.consume(self.approvals, REF, CO, CASE3, REV, NOW, tenant_name="Test Tenant")
        self.assertEqual(ui.gate(self.inputs())[0], "approval_consumed")

    def test_a_case_3_co_never_opens_the_create_even_with_both_constants_true(self):
        with mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            self.assertFalse(create_ui.locked())  # both constants are True here
            self.assertIsNotNone(create_ui.gate(self.inputs())[0])
            html = create_ui.create_card_html(self.inputs())
        self.assertNotIn("Create in production", html)
        self.assertFalse(any(tag in html for tag in FORBIDDEN))


class CardTests(Base):
    def test_only_a_case_3_co_gets_a_card(self):
        self.unlocked()
        for other in (row(Onboarding_Product__c="Credential Exposure"), row(Onboarding_Product__c="Surface"),
                      row(Onboarding_Type__c="Renewal"), None):
            self.assertEqual(ui.card_html(self.inputs(row=other)), "")

    def test_locked_shows_the_reason_and_no_control(self):
        html = ui.card_html(self.inputs())
        self.assertIn("Locked", html)
        self.assertIn("locked in this build", html)
        self.assertFalse(any(tag in html for tag in FORBIDDEN))
        self.assertEqual(ui.card_html(self.inputs(vm=True)), "")

    def test_ready_shows_one_form_for_the_dry_run_only(self):
        self.unlocked()
        html = ui.card_html(self.inputs())
        self.assertIn("action='" + ui.DRY_RUN_PATH + "'", html)
        self.assertIn("Run production dry run", html)
        self.assertIn("production_dry_run_authorized", html)
        self.assertEqual(html.count("<form"), 1)
        self.assertNotIn("Create in production", html)
        self.assertIn("nothing is submitted", html.lower())

    def test_blocked_names_the_first_closed_gate_and_has_no_control(self):
        self.unlocked()
        html = ui.card_html(self.inputs(row=row(Onboarding_Approval_Status__c="Pending")))
        self.assertIn("Blocked", html)
        self.assertIn("not Approved", html)
        self.assertFalse(any(tag in html for tag in FORBIDDEN))

    def test_the_last_result_and_the_learn_only_findings_are_shown_in_plain_words(self):
        self.unlocked()
        self.record("dry_run_passed", when=NOW - timedelta(hours=1),
                    form_fields_checked={"text.company_name": "pass", "advanced_text.maximum_scan_duration_hours": "pass"},
                    observations={"max_scan_duration_control": "present", "max_scan_duration_default": "24",
                                  "scan_now_control": "absent"})
        html = ui.card_html(self.inputs())
        for word in ("passed. Nothing was created", "Maximum scan Duration control: present", "form default 24 h", "Scan now"):
            self.assertIn(word, html)
        self.assertNotIn("(default 24 h)", html)  # 24 h is the FORM's default, never the value that is set
        self.assertIn("Maximum scan Duration set to 90 h and read back", html)  # from the passing advanced_text field
        self.record("refused", when=NOW - timedelta(minutes=5), refusal_reason="max_scan_duration_control_absent",
                    observations={"max_scan_duration_control": "absent"})
        html = ui.card_html(self.inputs())
        self.assertIn("max_scan_duration_control_absent", html)
        self.assertIn("control: absent", html)

    def test_the_90_hour_line_needs_the_passing_field_and_a_passing_run(self):
        self.unlocked()
        self.record("refused", refusal_reason="advanced_mismatch", form_fields_checked={
            "advanced_text.maximum_scan_duration_hours": "pass"}, observations={"max_scan_duration_default": "24"})
        self.assertNotIn("read back", ui.card_html(self.inputs()))  # a refused run proves nothing was verified
        self.record("dry_run_passed", when=NOW + timedelta(minutes=1), form_fields_checked={
            "advanced_text.maximum_scan_duration_hours": "fail"})
        self.assertNotIn("set to 90 h", ui.card_html(self.inputs()))

    def test_a_started_run_shows_the_progress_bar_and_no_form(self):
        self.unlocked()
        run = {"since": "2026-10-20T11:59:00Z", "running": True,
               "events": [{"t": "2026-10-20T11:59:05Z", "step": "pdr_source", "outcome": "ok"}]}
        html = ui.card_html(self.inputs(), run)
        self.assertIn("role='progressbar'", html)
        self.assertIn("Production dry run", html)
        self.assertFalse(any(tag in html for tag in ("<form", "<button")))


class PostTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        finished = mock.Mock()
        finished.poll.return_value = 0  # a process handle that has already ended
        self.launch = mock.Mock(return_value=finished)

    def values(self, **over):
        html = ui.card_html(self.inputs())
        data = {"reference": CO, "nonce": self.nonce(html), "production_dry_run_authorized": "1"}
        data.update(over)
        return data

    def test_a_good_post_launches_once_with_the_case_3_route(self):
        self.assertEqual(ui.handle_post(self.values(), self.inputs(), self.launch), "launched")
        reference, revision, route, env = self.launch.call_args.args
        self.assertEqual((reference, revision, route), (CO, REV, CASE3))
        self.assertEqual(env[create_ui.SESSIONS_ENV], "1")
        self.assertEqual(env["SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"], REF)
        self.launch.assert_called_once()

    def test_the_token_is_single_use_and_bound(self):
        values = self.values()
        self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "launched")
        self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "confirmation_invalid")
        self.assertEqual(ui.handle_post(self.values(nonce="forged"), self.inputs(), self.launch), "confirmation_invalid")
        self.launch.assert_called_once()

    def test_the_authorization_box_is_required(self):
        for value in (None, "0", "yes"):
            self.setUp()
            self.unlocked()
            values = self.values(production_dry_run_authorized=value) if value is not None else \
                {k: v for k, v in self.values().items() if k != "production_dry_run_authorized"}
            self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "authorization_missing")
            self.launch.assert_not_called()

    def test_every_gate_is_rejudged_at_the_post_and_a_refusal_spends_the_token(self):
        values = self.values()
        self.assertEqual(ui.handle_post(values, self.inputs(row=row(Onboarding_Approval_Status__c="Pending")), self.launch),
                         "salesforce_not_approved")
        self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "confirmation_invalid")
        self.launch.assert_not_called()

    def test_a_wrong_reference_or_a_locked_build_launches_nothing(self):
        self.assertEqual(ui.handle_post(self.values(reference="CO-0001"), self.inputs(), self.launch), "invalid_request")
        values = self.values()
        with mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
            self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "production_dry_run_not_supported")
        self.launch.assert_not_called()

    def test_a_failed_start_and_a_second_concurrent_run_are_reported(self):
        failing = mock.Mock(return_value=None)
        self.assertEqual(ui.handle_post(self.values(), self.inputs(), failing), "launch_failed")
        values = self.values()  # the card (and its token) is rendered before another run starts
        running = mock.Mock()
        running.poll.return_value = None
        create_ui.LAUNCHES.claim(lambda: running)
        self.assertEqual(ui.handle_post(values, self.inputs(), self.launch), "launch_in_progress")
        self.launch.assert_not_called()
        self.assertIn("already in progress", ui.card_html(self.inputs()))  # and the card shows no form while it runs
        self.assertNotIn("<form", ui.card_html(self.inputs()))


class ProgressTests(Base):
    def events(self, *steps):
        return [{"t": "2026-10-20T11:59:%02dZ" % i, "step": step, "outcome": "ok"} for i, step in enumerate(steps)]

    def test_the_table_has_real_steps_and_the_route_selects_it(self):
        view = run_progress.build_progress(run_progress.PRODUCTION_DRY_RUN_ROUTE, [])
        self.assertEqual(len(view.steps), 6)
        self.assertEqual(view.current, "Starting…")
        for step in ("pdr_source", "pdr_gate", "pdr_signed_in", "pdr_duplicates", "pdr_form", "pdr_recorded"):
            self.assertIn(step, run_progress.MILESTONE_STEPS)

    def test_progress_is_only_what_was_recorded(self):
        none = ui.progress_view([], None, running=True)
        some = ui.progress_view(self.events("pdr_source", "pdr_gate"), None, running=True)
        more = ui.progress_view(self.events("pdr_source", "pdr_gate", "pdr_signed_in", "pdr_duplicates"), None, running=True)
        self.assertLess(none["percent"], some["percent"])
        self.assertLess(some["percent"], more["percent"])
        self.assertLessEqual(more["percent"], 95)
        self.assertIn("Signing in", some["current"])  # waiting for SSO + MFA is the active step after the gates
        self.assertEqual((some["done"], some["failed"]), (False, None))

    def test_a_passed_record_is_100_and_a_refusal_names_its_reason(self):
        done = ui.progress_view(self.events("pdr_source"), {"outcome": "dry_run_passed"}, running=False)
        self.assertEqual((done["percent"], done["done"], done["failed"]), (100, True, None))
        failed = ui.progress_view(self.events("pdr_source", "pdr_gate", "pdr_signed_in"),
                                  {"outcome": "refused", "refusal_reason": "duplicate_in_production"}, running=False)
        self.assertEqual((failed["done"], failed["failed"]), (False, "duplicate_in_production"))
        self.assertIn("Checking production for duplicates", failed["current"])

    def test_a_run_that_ended_without_a_record_is_a_failure_not_a_hang(self):
        gone = ui.progress_view(self.events("pdr_source"), None, running=False)
        self.assertEqual(gone["failed"], "runner_ended_without_a_record")

    def test_the_latest_result_since_the_launch_is_used_and_older_ones_never_count(self):
        self.record("dry_run_passed", when=NOW - timedelta(hours=3))
        self.assertIsNone(ui.result_since(self.dir, CO, "2026-10-20T11:00:00Z"))
        self.record("refused", when=NOW - timedelta(minutes=10), refusal_reason="clone_stale")
        found = ui.result_since(self.dir, CO, "2026-10-20T11:00:00Z")
        self.assertEqual((found["outcome"], found["refusal_reason"]), ("refused", "clone_stale"))


if __name__ == "__main__":
    unittest.main()
