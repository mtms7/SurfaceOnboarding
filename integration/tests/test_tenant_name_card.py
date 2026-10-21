"""The owner confirms the short tenant name on the CO card (docs/60; owner decision 2026-10-21).

The summary card shows the proposal with a one-field form for a long account name (and the confirmed name with a way to change it),
nothing for a short name. The POST validates against a FRESH Salesforce read of the account name, records the decision, and starts
nothing. Hermetic: fake reads, temporary state files, no browser.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_case3_summary as summary
from integration.onboarding import tenant_name_decision as decision
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run_ui as base
import tools.attended_ce_only_playwright as runner

FULL = "Barmherzige Brüder gemeinnützige Träger GmbH"
SHORT = "Barmherzige Brüder gemeinnützige TG"
REV = base.REV


def plan(company, full=None):
    value = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
    value["texts"]["Company name"] = company
    if full is not None:
        value["tenant_full_name"] = full
    return value


def flat(block):
    return dict(block)


class SummaryTests(unittest.TestCase):
    def test_a_confirmed_name_is_shown_as_confirmed_and_the_account_name_stays_the_salesforce_one(self):
        result = summary.build(plan(SHORT, FULL))
        self.assertEqual(flat(result["salesforce"])["Account name"], FULL)
        tenant = flat(result["tenant"])
        self.assertEqual(tenant["Tenant name"], SHORT)
        self.assertEqual(tenant["Short name (docs/60)"], "Confirmed: " + SHORT)
        self.assertFalse([a for a in result["attention"] if "short name" in a.lower()])

    def test_an_unconfirmed_long_name_still_proposes(self):
        result = summary.build(plan(FULL, FULL))
        self.assertIn("Proposed: " + SHORT, flat(result["tenant"])["Short name (docs/60)"])
        self.assertTrue([a for a in result["attention"] if "short name" in a.lower()])

    def test_the_full_name_key_is_optional(self):
        result = summary.build(plan(FULL))
        self.assertIn("Proposed: " + SHORT, flat(result["tenant"])["Short name (docs/60)"])


class CardTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / decision.STATE_FILE_NAME
        patcher = mock.patch.object(runner, "TENANT_NAMES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def card(self, value):
        with mock.patch.object(dashboard, "case3_values", mock.Mock(return_value=(REV, value))):
            return dashboard._case3_values_card("CO-0800", base.row())

    def test_a_long_name_shows_a_one_field_form_prefilled_with_the_proposal(self):
        html = self.card(plan(FULL, FULL))
        self.assertIn("action='/attended/tenant-name-confirm'", html)
        self.assertIn("name='reference' value='CO-0800'", html)
        self.assertIn("name='tenant_name' value='" + SHORT + "'", html)
        self.assertIn("Confirm tenant name", html)

    def test_a_confirmed_name_shows_the_form_prefilled_with_it_to_change_it(self):
        html = self.card(plan(SHORT, FULL))
        self.assertIn("name='tenant_name' value='" + SHORT + "'", html)

    def test_a_short_name_shows_no_form(self):
        html = self.card(plan("Advanced Technology International", "Advanced Technology International"))
        self.assertNotIn("tenant-name-confirm", html)
        self.assertNotIn("<form", html)

    def test_the_prefill_is_escaped(self):
        evil = plan(FULL, FULL)
        with mock.patch.object(dashboard.case3_summary.account_name_shortener, "propose",
                               return_value={"proposed": "x' onfocus='alert(1)", "changed": True, "steps": [], "reason": "shortened"}):
            html = self.card(evil)
        self.assertNotIn("onfocus='alert", html)

    def test_the_route_is_a_post_route(self):
        self.assertIn("/attended/tenant-name-confirm", dashboard.POST_ROUTES)


class CreatedCoTests(unittest.TestCase):
    """A CO that already has its Account UUID / Surface Account ID keeps a name form: scan status and validation find the tenant by name."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / decision.STATE_FILE_NAME
        patcher = mock.patch.object(runner, "TENANT_NAMES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def card(self, **over):
        with mock.patch.object(dashboard, "case3_values", mock.Mock(side_effect=AssertionError("no plan for a created CO"))):
            return dashboard._case3_values_card("CO-0800", base.row(**over))

    def test_a_created_co_still_says_do_not_onboard_and_shows_the_prefilled_form(self):
        html = self.card(Account_Name__c=FULL, Account_UUID__c="a" * 32)
        self.assertIn("Do not onboard", html)
        self.assertIn("action='/attended/tenant-name-confirm'", html)
        self.assertIn("name='tenant_name' value='" + SHORT + "'", html)
        self.assertIn("Confirm tenant name", html)

    def test_it_also_shows_for_a_surface_account_id_and_prefills_the_confirmed_name(self):
        decision.confirm(self.path, "CO-0800", FULL, "Barmherzige Brüder gemeinnützige Träger", "owner@example.test",
                         datetime(2026, 10, 21, tzinfo=timezone.utc))
        html = self.card(Account_Name__c=FULL, Surface_Account_ID__c="b" * 24)
        self.assertIn("Do not onboard", html)
        self.assertIn("name='tenant_name' value='Barmherzige Brüder gemeinnützige Träger'", html)

    def test_a_short_account_name_other_blockers_and_a_missing_name_show_no_form(self):
        for over in (dict(Account_Name__c="Advanced Technology International", Account_UUID__c="a" * 32),
                     dict(Account_Name__c=FULL, Onboarding_Approval_Status__c="Pending"),
                     dict(Account_UUID__c="a" * 32)):
            html = self.card(**over)
            self.assertIn("Do not onboard", html, over)
            self.assertNotIn("tenant-name-confirm", html, over)

    def test_the_prefill_is_escaped(self):
        with mock.patch.object(dashboard.case3_summary.account_name_shortener, "propose",
                               return_value={"proposed": "x' onfocus='alert(1)", "changed": True, "steps": [], "reason": "shortened"}):
            html = self.card(Account_Name__c=FULL, Account_UUID__c="a" * 32)
        self.assertNotIn("onfocus='alert", html)


class PostTests(unittest.TestCase):
    """``_post_tenant_name_confirm`` through a minimal handler double."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / decision.STATE_FILE_NAME
        patcher = mock.patch.object(runner, "TENANT_NAMES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, name, row=None):
        calls = {}

        class Handler:
            headers = {}

            def send_redirect(self, location):
                calls["redirect"] = location

            def send_page(self, status, body):
                calls["page"] = (int(status), body)

        row = row if row is not None else base.row(Account_Name__c=FULL)
        with mock.patch.object(dashboard, "detail_row", return_value=row):
            dashboard.Handler._post_tenant_name_confirm(Handler(), "CO-0800", {"tenant_name": [name]})
        return calls

    def test_a_valid_name_is_recorded_and_redirects_back_to_the_co_page(self):
        calls = self.post(SHORT)
        self.assertTrue(calls["redirect"].startswith("/onboard/CO-0800"), calls)
        self.assertEqual(decision.confirmed_name(self.path, "CO-0800", FULL), SHORT)

    def test_an_invalid_name_records_nothing_and_says_why(self):
        calls = self.post("Wrong Words gemeinnützige TG")
        self.assertEqual(calls["page"][0], 409)
        self.assertIn("tenant_name_first_words_changed", calls["page"][1])
        self.assertFalse(self.path.exists())

    def test_a_co_that_is_not_a_surface_route_is_refused(self):
        calls = self.post(SHORT, row=base.row(Account_Name__c=FULL, Onboarding_Product__c="Credential Exposure"))
        self.assertEqual(calls["page"][0], 404)
        self.assertFalse(self.path.exists())

    def test_a_missing_account_name_is_refused(self):
        calls = self.post(SHORT, row=base.row())
        self.assertEqual(calls["page"][0], 409)
        self.assertFalse(self.path.exists())


class DryRunButtonGateTests(unittest.TestCase):
    """The dashboard dry-run gate names the unconfirmed long name before any run is started (the runner refuses it too)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.names = Path(self.dir.name) / decision.STATE_FILE_NAME
        self.approvals = Path(self.dir.name) / "approvals.json"
        base.approval.issue(self.approvals, base.REF, base.CO, base.CASE3, REV, base.OWNER, frozenset({base.OWNER}),
                            base.NOW - base.timedelta(hours=1))
        for patch in (mock.patch.object(base.create_ui, "TOKENS", base.create_ui.OneTimeTokens()),
                      mock.patch.object(base.create_ui, "LAUNCHES", base.create_ui.LaunchRegistry()),
                      mock.patch.object(base.readiness, "PRODUCTION_DRY_RUN_SUPPORTED", True),
                      mock.patch.object(base.readiness, "PRODUCTION_RUNNER_SUPPORTED", False)):
            patch.start()
            self.addCleanup(patch.stop)

    def gate(self, account):
        inputs = base.create_ui.Inputs(
            reference=base.CO, row=base.row(Account_Name__c=account), now=base.NOW, environ={base.create_ui.SESSIONS_ENV: "1"},
            operator=base.OWNER, vm=False, approvals_path=self.approvals, records_parent=Path(self.dir.name),
            clone={"state": "fresh", "age_seconds": 3600}, sf_ready=True, row_fresh=True, tenant_names_path=self.names)
        return base.ui.gate(inputs)[0]

    def test_a_long_account_name_without_a_decision_blocks_with_a_plain_reason(self):
        self.assertEqual(self.gate(FULL), "tenant_name_not_confirmed")
        self.assertIn("short tenant name", base.ui.text("tenant_name_not_confirmed"))

    def test_a_confirmed_name_a_short_name_and_a_row_without_the_name_do_not_block(self):
        decision.confirm(self.names, base.CO, FULL, SHORT, "owner@example.test", datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertIsNone(self.gate(FULL))
        self.assertIsNone(self.gate("Advanced Technology International"))
        inputs = base.create_ui.Inputs(
            reference=base.CO, row=base.row(), now=base.NOW, environ={base.create_ui.SESSIONS_ENV: "1"}, operator=base.OWNER,
            vm=False, approvals_path=self.approvals, records_parent=Path(self.dir.name),
            clone={"state": "fresh", "age_seconds": 3600}, sf_ready=True, row_fresh=True)
        self.assertIsNone(base.ui.gate(inputs)[0])  # no account name in the row: the runner still gates

    def test_an_unreadable_decision_file_blocks_a_long_name(self):
        self.names.write_text("{", encoding="utf-8")
        self.assertEqual(self.gate(FULL), "tenant_name_not_confirmed")


if __name__ == "__main__":
    unittest.main()
