"""The confirmed short tenant name wired into the Surface / Case 3 source and the production dry run (owner decision 2026-10-21, docs/60).

A long account name needs the owner's confirmation before the dry run; the confirmed name replaces the tenant name everywhere the source is
used, while the primary-user alias stays derived from the full account name. Lookups of an existing tenant try the confirmed name and then
the full name. Hermetic: fake Salesforce replies, temporary state files, no browser.
"""

from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from integration.onboarding import production_dry_run as pdr
from integration.onboarding import tenant_name_decision as decision
from integration.tests import test_attended_ce_only_runner as base_runner
from integration.tests import test_case3_dry_run_runner as c3
import tools.attended_ce_only_playwright as runner

FULL = "Barmherzige Brüder gemeinnützige Träger GmbH"
SHORT = "Barmherzige Brüder gemeinnützige TG"
NOW = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)


class TempState(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / decision.STATE_FILE_NAME
        patcher = patch.object(runner, "TENANT_NAMES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def confirm(self):
        decision.confirm(self.path, "CO-0801", FULL, SHORT, "owner@example.test", NOW)


class SourceReadTests(TempState):
    CO = dict(base_runner.Case3RouteTests.CO, Account_Name__c=FULL)
    ROWS = base_runner.Case3RouteTests.ROWS

    def read(self, co=None):
        responses = [base_runner.CeFillSourceTests._completed(base_runner.CeFillSourceTests._sf_json([co or self.CO])),
                     base_runner.CeFillSourceTests._completed(base_runner.CeFillSourceTests._sf_json(self.ROWS))]
        with patch.object(runner.subprocess, "run", side_effect=responses), patch.object(runner, "_run_day", return_value=base_runner.RUN_DAY):
            return runner.case3_fill_source("CO-0801")

    def test_a_long_name_without_a_decision_keeps_the_full_name_and_is_flagged(self):
        source = self.read()
        self.assertEqual(source.tenant_name, FULL)
        self.assertIs(source.tenant_name_unconfirmed, True)

    def test_a_confirmed_name_replaces_the_tenant_name_but_not_the_alias(self):
        before = self.read().primary_user_alias
        self.confirm()
        source = self.read()
        self.assertEqual(source.tenant_name, SHORT)
        self.assertIs(source.tenant_name_unconfirmed, False)
        self.assertEqual(source.primary_user_alias, before)  # derived from the full account name, never from the short name

    def test_the_fill_plan_the_readback_and_the_company_name_use_the_confirmed_name(self):
        self.confirm()
        source = self.read()
        plan = runner.build_case3_fill(source, base_runner.RUN_DAY)
        self.assertEqual(plan["texts"]["Company name"], SHORT)
        self.assertEqual(runner.case3_production_intended(source, base_runner.RUN_DAY)["tenant_name"], SHORT)
        self.assertTrue(plan["texts"]["Organization Email"].endswith("+" + source.primary_user_alias + "@pentera.io"))

    def test_a_short_account_name_is_unchanged_and_never_flagged(self):
        source = self.read(dict(self.CO, Account_Name__c="Advanced Technology International"))
        self.assertEqual((source.tenant_name, source.tenant_name_unconfirmed), ("Advanced Technology International", False))

    def test_a_corrupt_decision_file_leaves_the_name_unconfirmed(self):
        self.path.write_text("{", encoding="utf-8")
        source = self.read()
        self.assertEqual((source.tenant_name, source.tenant_name_unconfirmed), (FULL, True))

    def test_the_flag_is_outside_equality_and_repr(self):
        a = self.read()
        self.confirm()
        b = self.read()
        self.assertEqual(a.surface.account_id, b.surface.account_id)
        self.assertNotIn("unconfirmed", repr(a.surface))


class DryRunGateTests(unittest.TestCase):
    def test_an_unconfirmed_long_name_is_refused_before_any_browser(self):
        h = c3.C3Harness(self)
        source = c3.make_case3_source()
        h.source = dataclasses.replace(source, surface=dataclasses.replace(source.surface, tenant_name_unconfirmed=True))
        out = h.go()
        self.assertEqual((out["result"], out["outcome"]), ("tenant_name_not_confirmed", "refused"))
        self.assertEqual((h.opened, h.page.clicks), (0, []))
        self.assertIsNone(h.case3_approval_unused())

    def test_a_confirmed_name_runs_and_types_that_name(self):
        h = c3.C3Harness(self)
        source = c3.make_case3_source()
        h.source = dataclasses.replace(source, surface=dataclasses.replace(source.surface, tenant_name=SHORT))
        out = h.go()
        self.assertEqual(out["outcome"], "dry_run_passed")

    def test_the_reason_is_a_known_code(self):
        self.assertEqual(pdr.known_reason("tenant_name_not_confirmed"), "tenant_name_not_confirmed")


class LookupNamesTests(TempState):
    def test_lookups_of_an_existing_tenant_try_the_confirmed_name_then_the_full_name(self):
        self.assertEqual(runner.existing_tenant_names(FULL, "CO-0801"), (FULL,))
        self.confirm()
        self.assertEqual(runner.existing_tenant_names(FULL, "CO-0801"), (SHORT, FULL))
        self.assertEqual(runner.existing_tenant_names("Advanced Technology International", "CO-0801"),
                         ("Advanced Technology International",))

    def test_a_corrupt_file_falls_back_to_the_full_name_only(self):
        self.path.write_text("{", encoding="utf-8")
        self.assertEqual(runner.existing_tenant_names(FULL, "CO-0801"), (FULL,))


if __name__ == "__main__":
    unittest.main()
