"""Path B step (b): a Case 3 production approval is bound to the owner-confirmed tenant name (owner go 2026-10-22).

* ``issue`` requires a tenant name for the Case 3 route and refuses one for CE-only (CE-only records are unchanged);
* ``tenant_name_problem`` and ``consume`` refuse a Case 3 approval whose recorded name differs from the name the create
  would type (``approval_tenant_name_drift``) or that has none (``approval_tenant_name_missing``, fail closed);
* the live Case 3 create gate runs that check after the approval check and never for any other route;
* the issuing tool resolves the name from the account name and the confirmed-name file and refuses an unconfirmed long name.

Hermetic: temporary folders, fake rows and fake gate inputs; no Salesforce, no network, no browser.
"""

import contextlib
import inspect
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from integration.onboarding import production_approval as pa
from integration.onboarding import production_create as pc
from integration.onboarding import tenant_name_decision as tnd
from integration.tests import test_production_create as base
from integration.tests.test_production_create import make_inputs, target_ok
from tools import issue_production_approval as tool

NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "owner@example.test"
ISSUERS = frozenset({OWNER})
CO = "CO-0802"
REV = "2026-10-22T08:00:00.000+0000"
REF = "PROD-C3-0802-20261022"
CASE3 = pa.CASE3_ROUTE
CE = pa.ALLOWED_ROUTE
NAME = "Barmherzige Brüder gemeinnützige TG"
FULL = "Barmherzige Brüder gemeinnützige Träger GmbH"


class ApprovalNameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / pa.STATE_FILE_NAME

    def issue(self, route=CASE3, ref=REF, **over):
        args = dict(approval_ref=ref, co_reference=CO, route=route, source_revision=REV, issued_by=OWNER,
                    allowed_issuers=ISSUERS, now=NOW)
        if route == CASE3:
            args["tenant_name"] = NAME
        args.update(over)
        return pa.issue(self.path, **args)

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_a_case_3_record_stores_the_name(self):
        record = self.issue()
        self.assertEqual(record["tenant_name"], NAME)
        self.assertEqual(self.stored()[REF]["tenant_name"], NAME)

    def test_a_case_3_issue_without_a_valid_name_is_refused_and_writes_nothing(self):
        for bad in (None, "", " ", " padded ", "two  spaces", "line\nbreak", "tab\there", "x" * 81, 7, ["a"]):
            with self.subTest(name=bad):
                with self.assertRaises(pa.ApprovalRefused) as caught:
                    self.issue(tenant_name=bad)
                self.assertEqual(caught.exception.reason, "invalid_tenant_name")
        self.assertFalse(self.path.exists())

    def test_a_ce_only_record_takes_no_name(self):
        record = self.issue(route=CE)
        self.assertNotIn("tenant_name", record)
        with self.assertRaises(pa.ApprovalRefused) as caught:
            self.issue(route=CE, ref="PROD-CE-0803-20261022", tenant_name=NAME, co_reference="CO-0803")
        self.assertEqual(caught.exception.reason, "tenant_name_not_allowed")

    def test_a_ce_record_carrying_a_name_is_unreadable(self):
        self.issue(route=CE)
        data = self.stored()
        data[REF]["tenant_name"] = NAME
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(pa.check(self.path, REF, CO, CE, REV, NOW), "approval_unreadable")

    def test_tenant_name_problem(self):
        self.issue()
        self.assertIsNone(pa.tenant_name_problem(self.path, REF, CO, CASE3, NAME))
        self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CASE3, NAME + " "), "approval_tenant_name_drift")
        self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CASE3, FULL), "approval_tenant_name_drift")
        for bad in (None, "", 5):
            self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CASE3, bad), "approval_tenant_name_drift")
        self.assertEqual(pa.tenant_name_problem(self.path, "PROD-C3-0000-20261022", CO, CASE3, NAME), "approval_missing")
        self.assertEqual(pa.tenant_name_problem(self.path, REF, "CO-0999", CASE3, NAME), "approval_co_mismatch")
        self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CE, NAME), "approval_route_mismatch")

    def test_unreadable_state_fails_closed(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CASE3, NAME), "approval_unreadable")

    def test_a_legacy_case_3_record_without_a_name_loads_but_never_passes_the_name_check(self):
        self.issue()
        data = self.stored()
        del data[REF]["tenant_name"]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(pa.check(self.path, REF, CO, CASE3, REV, NOW))
        self.assertEqual(pa.tenant_name_problem(self.path, REF, CO, CASE3, NAME), "approval_tenant_name_missing")
        self.assertEqual(pa.consume(self.path, REF, CO, CASE3, REV, NOW, tenant_name=NAME), "approval_tenant_name_missing")
        self.assertNotIn("consumed_at", self.stored()[REF])

    def test_consume_of_case_3_needs_the_exact_name_and_marks_nothing_otherwise(self):
        self.issue()
        self.assertEqual(pa.consume(self.path, REF, CO, CASE3, REV, NOW), "approval_tenant_name_missing")
        self.assertEqual(pa.consume(self.path, REF, CO, CASE3, REV, NOW, tenant_name="Other Name"),
                         "approval_tenant_name_drift")
        self.assertNotIn("consumed_at", self.stored()[REF])
        self.assertIsNone(pa.consume(self.path, REF, CO, CASE3, REV, NOW, tenant_name=NAME))
        self.assertIn("consumed_at", self.stored()[REF])
        self.assertEqual(pa.consume(self.path, REF, CO, CASE3, REV, NOW, tenant_name=NAME), "approval_consumed")

    def test_consume_of_ce_only_is_unchanged(self):
        self.issue(route=CE)
        self.assertIsNone(pa.consume(self.path, REF, CO, CE, REV, NOW))

    def test_check_compares_the_name_only_when_one_is_given(self):
        self.issue()
        self.assertIsNone(pa.check(self.path, REF, CO, CASE3, REV, NOW))
        self.assertIsNone(pa.check(self.path, REF, CO, CASE3, REV, NOW, tenant_name=NAME))
        self.assertEqual(pa.check(self.path, REF, CO, CASE3, REV, NOW, tenant_name="Other"), "approval_tenant_name_drift")

    def test_a_second_approval_for_the_same_revision_is_refused_even_with_a_changed_name(self):
        self.issue()
        with self.assertRaises(pa.ApprovalRefused) as caught:
            self.issue(ref="PROD-C3-0802-20261023", tenant_name="Barmherzige Brüder gemeinnützige T")
        self.assertEqual(caught.exception.reason, "approval_already_active_for_co")


def gate(inputs, name_fn=None, **kwargs):
    extra = {} if name_fn is None else {"approval_name_fn": name_fn}
    return pc.evaluate_gate(inputs, production_target_fn=target_ok, approval_check_fn=base.approval_ok, **extra, **kwargs)


class GateNameTests(unittest.TestCase):
    def test_the_live_case_3_gate_runs_the_name_check_with_the_name_the_create_would_type(self):
        seen = []

        def fn(path, ref, co, route, name):
            seen.append((ref, co, route, name))
            return None
        inputs = make_inputs(route=CASE3, expected_tenant_name=NAME)
        self.assertIsNone(gate(inputs, fn, case3_create=True))
        self.assertEqual(seen, [(inputs.approval_ref, inputs.co_reference, CASE3, NAME)])

    def test_a_name_problem_is_the_refusal(self):
        reason = gate(make_inputs(route=CASE3), lambda *args: "approval_tenant_name_drift", case3_create=True)
        self.assertEqual(reason, "approval_tenant_name_drift")

    def test_the_name_check_runs_after_the_approval_and_the_revision_gates(self):
        reason = gate(make_inputs(route=CASE3, source_revision_now="r30"), lambda *a: "approval_tenant_name_drift",
                      case3_create=True)
        self.assertEqual(reason, "approval_revision_drift")

    def test_the_name_check_never_runs_for_ce_only_or_a_dry_run(self):
        def forbidden(*args):
            raise AssertionError("name check must not run")
        self.assertIsNone(gate(make_inputs(route=CE), forbidden))
        self.assertIsNone(gate(make_inputs(route=CE), forbidden, case3_create=True))
        self.assertIsNone(gate(make_inputs(route=CASE3), forbidden, dry_run=True))

    def test_the_default_check_is_the_approval_module_function(self):
        self.assertIs(inspect.signature(pc.evaluate_gate).parameters["approval_name_fn"].default, pa.tenant_name_problem)


ROW = {"Name": CO, "Onboarding_Product__c": "Surface & Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
       "Onboarding_Approval_Status__c": "Approved", "Main_Domain__c": "customer.example",
       "Email_Domains__c": "@customer-mail.example", "Account_UUID__c": None, "Surface_Account_ID__c": None,
       "LastModifiedDate": REV, "Account_Name__c": "Customer Co."}
ORG = "00D1t000000ABCDEAA"
ENV = {"SURFACE_ONBOARDING_APPROVAL_ISSUERS": OWNER, "SURFACE_SF_EXPECTED_ORG_ID": ORG}


class IssueToolNameTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.state = self.dir / pa.STATE_FILE_NAME
        self.names = self.dir / tnd.STATE_FILE_NAME

    def run_tool(self, row=None, extra=()):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = tool.main(["--co", CO, *extra], reader=lambda co: dict(ROW if row is None else row),
                             now_fn=lambda: NOW, environ=dict(ENV), input_fn=lambda p="": f"ISSUE {CO}",
                             isatty_fn=lambda: True, state_path=self.state, org_probe=lambda: ORG,
                             tenant_names_path=self.names)
        return code, out.getvalue()

    def records(self):
        return json.loads(self.state.read_text(encoding="utf-8")) if self.state.exists() else {}

    def test_a_short_name_needs_no_confirmation_and_is_bound_as_the_create_would_type_it(self):
        code, out = self.run_tool()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records()[REF]["tenant_name"], "Customer Co")  # trailing period removed, as the runner does
        self.assertIn("Tenant name: Customer Co", out)

    def test_a_long_unconfirmed_name_is_refused_and_writes_nothing(self):
        code, out = self.run_tool(dict(ROW, Account_Name__c=FULL))
        self.assertEqual(code, 2)
        self.assertIn('"reason":"tenant_name_unconfirmed"', out)
        self.assertEqual(self.records(), {})

    def test_a_confirmed_long_name_is_bound(self):
        tnd.confirm(self.names, CO, FULL, NAME, OWNER, NOW)
        code, out = self.run_tool(dict(ROW, Account_Name__c=FULL))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.records()[REF]["tenant_name"], NAME)

    def test_a_name_confirmed_for_another_account_name_does_not_count(self):
        tnd.confirm(self.names, CO, FULL, NAME, OWNER, NOW)
        code, out = self.run_tool(dict(ROW, Account_Name__c=FULL + " Holding"))
        self.assertEqual(code, 2)
        self.assertIn("tenant_name_unconfirmed", out)

    def test_an_unreadable_name_file_is_refused(self):
        self.names.write_text("{broken", encoding="utf-8")
        code, out = self.run_tool(dict(ROW, Account_Name__c=FULL))
        self.assertEqual(code, 2)
        self.assertIn("tenant_names_unreadable", out)
        self.assertEqual(self.records(), {})

    def test_a_missing_account_name_is_refused(self):
        code, out = self.run_tool(dict(ROW, Account_Name__c=None))
        self.assertEqual(code, 2)
        self.assertIn("account_name_unavailable", out)

    def test_the_dry_run_prints_the_name_and_writes_nothing(self):
        code, out = self.run_tool(extra=("--dry-run",))
        self.assertEqual(code, 0, out)
        self.assertIn("Tenant name: Customer Co", out)
        self.assertEqual(self.records(), {})

    def test_a_ce_only_approval_has_no_name_and_does_not_read_the_name_file(self):
        self.names.write_text("{broken", encoding="utf-8")
        row = dict(ROW, Onboarding_Product__c="Credential Exposure", Onboarding_Type__c="New Product Onboarding")
        code, out = self.run_tool(row)
        self.assertEqual(code, 0, out)
        self.assertNotIn("tenant_name", self.records()["PROD-CE-0802-20261022"])
        self.assertNotIn("Tenant name", out)


if __name__ == "__main__":
    unittest.main()
