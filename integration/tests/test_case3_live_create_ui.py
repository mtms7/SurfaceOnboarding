"""Path B (owner go 2026-10-22): the dashboard side of the guarded LIVE Case 3 create (Surface & Credential Exposure, New).

The Case 3 create opens only when BOTH hard-coded lock constants are True (patched here, never changed) AND the CO row is the Case 3
route AND a one-CO Case 3 approval bound to the owner-confirmed tenant name is valid AND a passing dry run of that approval is recorded
AND the Account UUID and Surface Account ID are empty, plus every CE-only gate. Nothing is automatic after the create: the IDs and the
stage are written by the owner's separate review and confirmation (case3_followup). Fakes only; nothing outside a temporary folder.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_approval as approval
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import session_readiness as readiness

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
CO = "CO-0802"
REF = "PROD-C3-0802-20261022"
REV = "2026-10-22T09:00:00.000+0000"
OWNER = "owner@example.test"
NAME = "Example Klinik gGmbH"
CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
FRESH_CLONE = {"state": "fresh", "age_seconds": 3600}
FORBIDDEN_TAGS = ("<form", "<button", "<input", "<script", "<select", "<textarea", "onclick", "method='post'", "<a ", "href=")
ACCOUNT_ID = "0123456789abcdef01234567"
UUID = "0123456789abcdef0123456789abcdef"


def row(**over):
    data = {"Id": "a0B5g00000AbCdEFGH", "Name": CO, "LastModifiedDate": REV,
            "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
            "Account_UUID__c": None, "Surface_Account_ID__c": None, "Account_Name__c": "Example Klinik gGmbH"}
    data.update(over)
    return data


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.approvals = self.dir / "approvals.json"
        approval.issue(self.approvals, REF, CO, CASE3, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1), tenant_name=NAME)
        self.record(outcome="dry_run_passed", when=NOW - timedelta(hours=2))
        for patch in (mock.patch.object(ui, "TOKENS", ui.OneTimeTokens()), mock.patch.object(ui, "LAUNCHES", ui.LaunchRegistry()),
                      mock.patch.object(fu, "TOKENS", ui.OneTimeTokens()),
                      mock.patch.dict("os.environ", {"SURFACE_ONBOARDING_TICKET_ROOT": str(self.dir / "ticket-root")})):
            patch.start()
            self.addCleanup(patch.stop)

    def record(self, outcome, when=NOW, route=CASE3, **extra):
        data = {"co_reference": CO, "approval_ref": REF, "outcome": outcome, "route": route, "salesforce_revision": REV, **extra}
        return pcr.record(self.dir, data, when)

    def inputs(self, **over):
        data = dict(reference=CO, row=row(), now=NOW, environ={ui.SESSIONS_ENV: "1"}, operator=OWNER, vm=False,
                    approvals_path=self.approvals, records_parent=self.dir, clone=dict(FRESH_CLONE), sf_ready=True,
                    row_fresh=True, case3_tenant_name=NAME)
        data.update(over)
        return ui.Inputs(**data)

    def unlocked(self, runner=True, dry=True):
        for patch in (mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", runner),
                      mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry)):
            patch.start()
            self.addCleanup(patch.stop)

    def has_control(self, html):
        return any(tag in html for tag in FORBIDDEN_TAGS)


class WordingTests(Base):
    def test_a_verified_case_3_create_never_says_to_write_the_uuid_the_ce_only_way(self):
        # While locked (the build default) the card is one read-only status; for Case 3 it must not carry the CE-only next step.
        self.record(outcome="readback_verified", account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME)
        html = ui.create_card_html(self.inputs())
        self.assertIn("Created and verified", html)
        self.assertNotIn("write the Account UUID to Salesforce", html)
        self.assertIn("Account ID", html)
        self.assertFalse(self.has_control(html))

    def test_the_ce_only_status_text_is_unchanged(self):
        self.assertIn("write the Account UUID to Salesforce", ui.STATUS_TEXT[ui.CREATED_VERIFIED][2])


class LockedTests(Base):
    def test_the_locks_are_false_and_a_case_3_co_shows_and_launches_nothing(self):
        self.assertIs(readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run only unlocked for the CO-0802 dry run; the create stays locked
        self.assertEqual(ui.create_card_html(self.inputs()), "")
        launch = mock.Mock()
        values = {"reference": CO, "nonce": "x", "confirm_reference": CO, "production_create_authorized": "1"}
        self.assertEqual(ui.handle_create_post(values, self.inputs(), launch), "production_runner_not_supported")
        launch.assert_not_called()

    def test_one_constant_is_not_enough(self):
        for runner, dry in ((True, False), (False, True)):
            self.setUp()
            self.unlocked(runner, dry)
            self.assertEqual(ui.create_card_html(self.inputs()), "")
            self.assertIsNotNone(ui.gate(self.inputs())[0])


class GateTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()

    def test_every_gate_passing_opens_the_case_3_create(self):
        reason, ctx = ui.gate(self.inputs())
        self.assertIsNone(reason)
        self.assertEqual((ctx["route"], ctx["tenant_name"]), (CASE3, NAME))

    def refuses(self, reason, **over):
        found, _ = ui.gate(self.inputs(**over))
        self.assertEqual(found, reason)
        self.assertFalse(self.has_control(ui.create_card_html(self.inputs(**over))), reason)

    def test_each_case_3_gate_refuses_and_shows_no_control(self):
        self.refuses("tenant_name_not_confirmed", case3_tenant_name=None)
        self.refuses("tenant_name_not_confirmed", case3_tenant_name="")
        self.refuses("approval_tenant_name_drift", case3_tenant_name="Example Klinik gGmbH Holding")
        self.refuses("surface_account_id_present", row=row(Surface_Account_ID__c=ACCOUNT_ID))
        self.refuses("salesforce_prior_uuid_present", row=row(Account_UUID__c=UUID))
        self.refuses("operator_not_owner", operator="someone.else@example.test")
        self.refuses("approval_revision_drift", row=row(LastModifiedDate="2026-10-22T10:00:00.000+0000"))
        self.refuses("route_not_ce_only_new", row=row(Onboarding_Type__c="Renewal"))
        self.refuses("route_not_ce_only_new", row=row(Onboarding_Product__c="Surface"))
        self.refuses("salesforce_not_fresh", row_fresh=False)
        self.refuses("salesforce_session_not_ready", sf_ready=False)
        self.refuses("clone_stale", clone=None)
        self.refuses("production_locked", environ={})
        self.refuses("approval_expired", now=NOW + timedelta(days=8))

    def test_a_field_that_was_not_read_is_never_treated_as_empty(self):
        for field in ("Surface_Account_ID__c", "Account_UUID__c"):
            data = row()
            del data[field]
            self.refuses("surface_account_id_present" if field == "Surface_Account_ID__c" else "salesforce_prior_uuid_present",
                         row=data)

    def test_the_approval_must_be_a_case_3_approval_bound_to_a_name(self):
        legacy = self.dir / "legacy.json"
        approval.issue(legacy, REF, CO, CE, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1))
        self.refuses("approval_route_mismatch", approvals_path=legacy)
        # a legacy Case 3 record without a bound name never opens the create
        import json
        data = json.loads(self.approvals.read_text(encoding="utf-8"))
        del data[REF]["tenant_name"]
        self.approvals.write_text(json.dumps(data), encoding="utf-8")
        self.refuses("approval_tenant_name_missing")

    def test_a_ce_only_co_with_a_case_3_approval_is_refused(self):
        found, _ = ui.gate(self.inputs(row=row(Onboarding_Product__c="Credential Exposure")))
        self.assertEqual(found, "approval_route_mismatch")

    def test_the_dry_run_gates_apply_to_case_3(self):
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        self.refuses("dry_run_required")
        self.record(outcome="dry_run_passed", when=NOW - timedelta(hours=25))
        self.refuses("dry_run_old")

    def test_a_prior_attempt_blocks(self):
        self.record(outcome="uncertain_submit")
        self.refuses("prior_attempt_needs_review")

    def test_the_ready_card_names_the_route_and_has_one_form(self):
        html = ui.create_card_html(self.inputs())
        for fragment in ("Create in production", "Surface &amp; Credential Exposure", "action='/attended/production-create'",
                         "name='confirm_reference'", "name='production_create_authorized'", "name='nonce'"):
            self.assertIn(fragment, html)
        self.assertEqual(html.count("<form"), 1)
        self.assertIn(NAME, html)  # the exact tenant name that will be typed

    def test_the_new_gate_texts_are_plain_sentences(self):
        for code in ("tenant_name_not_confirmed", "approval_tenant_name_drift", "approval_tenant_name_missing",
                     "surface_account_id_present"):
            self.assertNotIn("Not allowed:", ui.gate_text(code), code)


class PostTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.launched = []

    def launch(self, ref, revision, env, **kw):
        self.launched.append((ref, revision, env, kw))
        return SimpleNamespace(poll=lambda: None)

    def token(self, **over):
        return re.search(r"name='nonce' value='([^']+)'", ui.create_card_html(self.inputs(**over))).group(1)

    def values(self, **over):
        data = {"reference": CO, "nonce": self.token(), "confirm_reference": CO, "production_create_authorized": "1"}
        data.update(over)
        return data

    def test_a_complete_post_launches_once_naming_the_case_3_route(self):
        self.assertEqual(ui.handle_create_post(self.values(), self.inputs(), self.launch), "launched")
        self.assertEqual(len(self.launched), 1)
        ref, revision, env, kw = self.launched[0]
        self.assertEqual((ref, revision, kw), (CO, REV, {"route": CASE3}))
        self.assertEqual(env["SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"], REF)

    def test_the_token_is_bound_to_the_tenant_name(self):
        values = self.values()
        # the owner changed the confirmed name after the page was drawn: the approval no longer matches it
        self.assertEqual(ui.handle_create_post(values, self.inputs(case3_tenant_name="Other Name"), self.launch),
                         "approval_tenant_name_drift")
        self.assertEqual(self.launched, [])

    def test_the_token_is_single_use(self):
        values = self.values()
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launched")
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launch_in_progress")
        self.assertEqual(len(self.launched), 1)

    def test_bad_confirmations_refuse_without_launching(self):
        for over, reason in (({"confirm_reference": "CO-0797"}, "typed_reference_mismatch"),
                             ({"production_create_authorized": "0"}, "authorization_missing"),
                             ({"nonce": "wrong"}, "confirmation_invalid")):
            self.setUp()
            self.unlocked()
            self.launched.clear()
            self.assertEqual(ui.handle_create_post(self.values(**over), self.inputs(), self.launch), reason, over)
            self.assertEqual(self.launched, [], over)

    def test_a_ce_only_post_still_launches_with_the_three_argument_signature(self):
        ce_dir = tempfile.TemporaryDirectory()
        self.addCleanup(ce_dir.cleanup)
        ce_approvals = Path(ce_dir.name) / "approvals.json"
        approval.issue(ce_approvals, "PROD-CE-0802-20261022", CO, CE, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1))
        self.record(outcome="dry_run_passed", route=CE, when=NOW - timedelta(hours=2))
        pcr.record(self.dir, {"co_reference": CO, "approval_ref": "PROD-CE-0802-20261022", "outcome": "dry_run_passed",
                              "route": CE, "salesforce_revision": REV}, NOW - timedelta(hours=2))
        ce_row = row(Onboarding_Product__c="Credential Exposure")
        del ce_row["Surface_Account_ID__c"]
        inputs = self.inputs(row=ce_row, approvals_path=ce_approvals, case3_tenant_name=None)
        seen = []
        token = re.search(r"name='nonce' value='([^']+)'", ui.create_card_html(inputs)).group(1)
        values = {"reference": CO, "nonce": token, "confirm_reference": CO, "production_create_authorized": "1"}
        self.assertEqual(ui.handle_create_post(values, inputs, lambda r, v, e: seen.append((r, v)) or SimpleNamespace(poll=lambda: None)),
                         "launched")
        self.assertEqual(seen, [(CO, REV)])


class AfterCreateCardTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record(outcome="readback_verified", account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME,
                    domains=["example-klinik.example"])

    def test_a_verified_case_3_create_offers_the_follow_up_review_not_the_uuid_form(self):
        html = ui.create_card_html(self.inputs(row=row()))
        self.assertIn(ui.FOLLOWUP_REVIEW_PATH, html)
        self.assertNotIn(ui.UUID_REVIEW_PATH, html)
        self.assertNotIn(ui.CREATE_PATH, html)
        self.assertEqual(html.count("<form"), 1)
        self.assertNotIn(ACCOUNT_ID, html)
        self.assertNotIn(UUID, html)

    def test_the_follow_up_paths_are_registered_posts(self):
        self.assertIn(ui.FOLLOWUP_REVIEW_PATH, ui.POST_PATHS)
        self.assertIn(ui.FOLLOWUP_CONFIRM_PATH, ui.POST_PATHS)

    def test_a_finished_follow_up_shows_onboarded_and_no_form(self):
        now = NOW
        for step in ("ids", "stage"):
            fu._record(self.dir, CO, step, "write_started", now)
            fu._record(self.dir, CO, step, "written_verified", now)
        html = ui.create_card_html(self.inputs(row=row()))
        self.assertIn("Account Scanning", html)
        self.assertFalse(self.has_control(html))

    def test_an_uncertain_step_shows_its_text_and_no_form(self):
        fu._record(self.dir, CO, "ids", "write_started", NOW)
        fu._record(self.dir, CO, "ids", "write_uncertain", NOW)
        html = ui.create_card_html(self.inputs(row=row()))
        self.assertFalse(self.has_control(html))
        self.assertIn("check", html.lower())

    def test_the_ce_only_card_is_unchanged(self):
        ce_row = row(Onboarding_Product__c="Credential Exposure")
        html = ui.create_card_html(self.inputs(row=ce_row))
        self.assertNotIn(ui.FOLLOWUP_REVIEW_PATH, html)


class ProgressTests(Base):
    SINCE = "2026-10-22T11:00:00Z"

    def prog(self, running=True):
        return fu.progress(self.dir, CO, self.SINCE, running=running)

    def states(self, progress):
        return [state for _label, state in progress["steps"]]

    def test_nothing_recorded_is_waiting_for_the_runner(self):
        progress = self.prog()
        self.assertEqual((self.states(progress)[0], progress["done"], progress["failed"]), ("active", False, None))
        self.assertEqual(self.prog(running=False)["failed"], "runner_ended_without_a_record")

    def test_records_older_than_the_launch_never_count(self):
        self.record(outcome="readback_verified", when=datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(self.states(self.prog())[0], "active")

    def test_a_verified_create_waits_for_the_owner_review_without_failing(self):
        self.record(outcome="attempt_started", when=datetime(2026, 10, 22, 11, 5, tzinfo=timezone.utc))
        self.record(outcome="readback_verified", when=datetime(2026, 10, 22, 11, 10, tzinfo=timezone.utc),
                    account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME)
        progress = self.prog(running=False)
        self.assertEqual(self.states(progress)[:3], ["done", "done", "done"])
        self.assertEqual(self.states(progress)[3], "active")
        self.assertIsNone(progress["failed"])
        self.assertFalse(progress["done"])

    def test_the_follow_up_steps_complete_the_bar(self):
        self.record(outcome="readback_verified", when=datetime(2026, 10, 22, 11, 10, tzinfo=timezone.utc),
                    account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME)
        for step in ("ids", "stage"):
            fu._record(self.dir, CO, step, "write_started", NOW)
            fu._record(self.dir, CO, step, "written_verified", NOW)
        progress = self.prog(running=False)
        self.assertEqual((progress["done"], progress["percent"]), (True, 100))

    def test_failures_are_named_from_the_records(self):
        self.record(outcome="created_unverified", when=datetime(2026, 10, 22, 11, 10, tzinfo=timezone.utc),
                    refusal_reason="readback_contract_mismatch")
        self.assertEqual(self.prog(running=False)["failed"], "create_not_verified")
        self.setUp()
        self.record(outcome="refused", when=datetime(2026, 10, 22, 11, 10, tzinfo=timezone.utc), refusal_reason="duplicate_in_production")
        self.assertEqual(self.prog(running=False)["failed"], "duplicate_in_production")
        self.setUp()
        self.record(outcome="readback_verified", when=datetime(2026, 10, 22, 11, 10, tzinfo=timezone.utc),
                    account_id=ACCOUNT_ID, account_uuid=UUID, tenant_name=NAME)
        fu._record(self.dir, CO, "ids", "write_started", NOW)
        fu._record(self.dir, CO, "ids", "write_uncertain", NOW)
        self.assertEqual(self.prog(running=False)["failed"], "write_uncertain")


if __name__ == "__main__":
    unittest.main()
