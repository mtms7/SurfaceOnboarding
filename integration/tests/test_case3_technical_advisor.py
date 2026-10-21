"""The Case 3 Technical Advisor (owner 2026-10-21): the Account's "Technical Advisor" field (Delivery_Engineer__c, a User lookup) is read
with the fixed Case 3 source query through Account__r and shown in the Tenant creation summary, so the owner can pick the operator.

Read-only; the name is only displayed (escaped), never typed or written anywhere. A missing or odd value fails soft to the hand-read
text, never to a guessed name. Hermetic: fake Salesforce replies, no browser.
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest import mock
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_case3_summary as summary
from integration.tests import test_attended_ce_only_runner as base_runner
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run_ui as base
import tools.attended_ce_only_playwright as runner

REV = base.REV


def with_ta(co, name):
    account = {"Delivery_Engineer__r": {"Name": name} if name is not None else None}
    return dict(co, Account__r=account)


class SourceReadTests(unittest.TestCase):
    CO = base_runner.Case3RouteTests.CO
    ROWS = base_runner.Case3RouteTests.ROWS

    def read(self, co):
        responses = [base_runner.CeFillSourceTests._completed(base_runner.CeFillSourceTests._sf_json([co])),
                     base_runner.CeFillSourceTests._completed(base_runner.CeFillSourceTests._sf_json(self.ROWS))]
        with patch.object(runner.subprocess, "run", side_effect=responses) as run, \
                patch.object(runner, "_run_day", return_value=base_runner.RUN_DAY):
            source = runner.case3_fill_source("CO-0801")
        return source, run.call_args_list[0]

    def test_the_fixed_query_reads_the_technical_advisor_name_through_the_account(self):
        _source, first = self.read(with_ta(self.CO, "Ada Lovelace"))
        query = first.args[0][first.args[0].index("--query") + 1]
        self.assertIn("Account__r.Delivery_Engineer__r.Name", query)
        self.assertIn("FROM Customer_Onboarding__c WHERE Name = 'CO-0801'", query)

    def test_the_name_is_kept_cleaned_and_outside_equality_and_repr(self):
        source, _ = self.read(with_ta(self.CO, "  Ada   Lovelace "))
        self.assertEqual(source.technical_advisor, "Ada Lovelace")
        same, _ = self.read(with_ta(self.CO, "Someone Else"))
        self.assertEqual(source, same)
        self.assertNotIn("Ada", repr(source))

    def test_a_missing_or_odd_value_is_empty_never_a_guess(self):
        for co in (self.CO, with_ta(self.CO, None), dict(self.CO, Account__r=None), dict(self.CO, Account__r="x"),
                   with_ta(self.CO, ""), with_ta(self.CO, 42), with_ta(self.CO, "A" * 200), with_ta(self.CO, "bad\nname")):
            source, _ = self.read(co)
            self.assertEqual(source.technical_advisor, "", co.get("Account__r"))


class ValuesTests(unittest.TestCase):
    def test_case3_values_carries_the_name_beside_the_plan_without_changing_the_plan(self):
        source = dataclasses.replace(c3.make_case3_source(), technical_advisor="Ada Lovelace")
        with patch.object(runner, "case3_fill_source", return_value=source):
            _revision, plan = runner.case3_values("CO-0800", c3.RUN_DAY)
        self.assertEqual(plan["technical_advisor"], "Ada Lovelace")
        plain = runner.build_case3_fill(source, c3.RUN_DAY)
        extra = ("technical_advisor", "tenant_full_name")  # display-only keys (the second one: docs/60)
        self.assertEqual({k: v for k, v in plan.items() if k not in extra}, plain)
        self.assertEqual(plan["tenant_full_name"], source.account_name)
        for key in extra:
            self.assertNotIn(key, plain)


class SummaryTests(unittest.TestCase):
    def plan(self, name):
        plan = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
        if name is not None:
            plan["technical_advisor"] = name
        return plan

    def test_the_name_is_shown_and_the_operator_line_uses_it(self):
        result = summary.build(self.plan("Ada Lovelace"))
        facts, tenant = dict(result["salesforce"]), dict(result["tenant"])
        self.assertEqual(facts["Technical Advisor"], "Ada Lovelace")
        self.assertIn("Ada Lovelace", tenant["Operator Account"])
        line = " ".join(result["attention"])
        self.assertIn("Ada Lovelace", line)
        self.assertNotIn("not read", line)

    def test_without_a_name_the_hand_read_text_stays(self):
        for name in (None, "", "   "):
            result = summary.build(self.plan(name))
            self.assertEqual(dict(result["salesforce"])["Technical Advisor"], summary.TA_TEXT)
            self.assertIn("Technical Advisor", dict(result["tenant"])["Operator Account"])

    def test_a_non_text_name_fails_closed(self):
        with self.assertRaises(ValueError):
            summary.build(self.plan(5))


class CardTests(unittest.TestCase):
    def card(self, name):
        plan = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
        plan["technical_advisor"] = name
        with mock.patch.object(dashboard, "case3_values", mock.Mock(return_value=(REV, plan))):
            return dashboard._case3_values_card("CO-0800", base.row())

    def test_the_card_shows_the_escaped_name(self):
        html = self.card("Ada <b>Lovelace</b>")
        self.assertIn("Ada &lt;b&gt;Lovelace&lt;/b&gt;", html)
        self.assertNotIn("<b>Lovelace", html)


if __name__ == "__main__":
    unittest.main()
