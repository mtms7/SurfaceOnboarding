"""Production Surface renewal (owner go 2026-11-03, CO-0829): the pure edit rules, the guard and the Salesforce follow-up.

Local only: no Salesforce, BackOffice or network; every state file is temporary.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from integration.onboarding import production_create_live as live
from integration.onboarding import production_surface_renewal as renewal
from integration.onboarding import salesforce_case3_ids_writeback as ids_wb
from integration.onboarding import session_readiness as readiness
from integration.onboarding import surface_renewal_followup as followup

TODAY = date(2026, 11, 3)
ACCOUNT_ID = "a" * 24
ACCOUNT_UUID = "b" * 32
CO_ROW = {"Name": "CO-0829", "LastModifiedDate": "2026-11-02T21:49:32.000+0000", "Account_Name__c": "Royal Reesink",
          "Main_Domain__c": "royalreesink.com", "Onboarding_Product__c": "Surface",
          "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved"}
PLAN = {"engine": "surface_renewal", "blockers": [], "surface_term": {
    "start": "2026-11-01", "end": "2027-10-31", "apply_from": "2026-10-18", "tier": "go", "scanning_interval": "Monthly",
    "subdomains": 1000, "domains": 1000, "assets": 10000}}


def tenant_row(**changes):
    row = {"id": ACCOUNT_ID, "accountUuid": ACCOUNT_UUID, "accountName": "Royal Reesink", "accountDomain": "royalreesink.com",
           "isDeleted": False, "enabled": True, "scanningInterval": "NO_SCHEDULE", "alternateDomains": ["vanbergen.nl", "reesink.de"],
           "operatorAccounts": [{"operatorName": "Stefan Jansson Operator"}],
           "accountLicense": {"expirationDate": "2026-10-30T00:00:00Z", "startDate": "2023-11-23T00:00:00Z",
                              "assetsNumber": 10000, "domainsNumber": 143, "subDomainsNumber": 650}}
    for key, value in changes.items():
        if key.startswith("license_"):
            row["accountLicense"] = {**row["accountLicense"], key[len("license_"):]: value}
        else:
            row[key] = value
    return row


def reply(*rows, status=200):
    return {"status": status, "rows": list(rows)}


class IntendedTests(unittest.TestCase):
    def test_co_0829_targets(self):
        intended = renewal.intended_from(CO_ROW, PLAN, TODAY)
        self.assertEqual((intended.tenant_name, intended.primary_domain), ("Royal Reesink", "royalreesink.com"))
        self.assertEqual((intended.interval, intended.expiration), ("Monthly", date(2027, 10, 31)))
        self.assertEqual(intended.licensed, {"assets": 10000, "domains": 1000, "subdomains": 1000})

    def test_refusals(self):
        cases = [
            (dict(CO_ROW, Onboarding_Product__c="Credential Exposure"), PLAN, TODAY, "renewal_route_mismatch"),
            (dict(CO_ROW, Onboarding_Type__c="New Product Onboarding"), PLAN, TODAY, "renewal_route_mismatch"),
            (dict(CO_ROW, Onboarding_Approval_Status__c="Pending"), PLAN, TODAY, "renewal_not_approved"),
            (dict(CO_ROW, Main_Domain__c="not a domain"), PLAN, TODAY, "renewal_primary_domain_invalid"),
            (dict(CO_ROW, Account_Name__c=" "), PLAN, TODAY, "renewal_tenant_name_invalid"),
            (CO_ROW, dict(PLAN, blockers=["surface_term_ambiguous"]), TODAY, "renewal_blocked_surface_term_ambiguous"),
            (CO_ROW, dict(PLAN, surface_term=None), TODAY, "renewal_term_missing"),
            (CO_ROW, dict(PLAN, surface_term=dict(PLAN["surface_term"], scanning_interval=None)), TODAY,
             "renewal_interval_unknown"),
            (CO_ROW, PLAN, date(2026, 10, 17), "renewal_not_yet_applicable"),
            (CO_ROW, PLAN, date(2027, 10, 31), "renewal_expiration_in_past"),
            (CO_ROW, dict(PLAN, engine="case_6_renew_both"), TODAY, "renewal_route_mismatch"),
        ]
        for row, plan, today, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(renewal.RenewalRefused) as caught:
                    renewal.intended_from(row, plan, today)
                self.assertEqual(str(caught.exception), code)


class RowRuleTests(unittest.TestCase):
    def setUp(self):
        self.intended = renewal.intended_from(CO_ROW, PLAN, TODAY)

    def test_before_edit_finds_the_one_row(self):
        code, row = renewal.before_edit(reply(tenant_row()), self.intended)
        self.assertEqual(code, "ok")
        self.assertEqual(row["id"], ACCOUNT_ID)

    def test_before_edit_refusals(self):
        cases = [
            (reply(), "renewal_tenant_not_found"),
            (reply(tenant_row(), tenant_row(id="c" * 24, accountUuid="d" * 32)), "renewal_tenant_ambiguous"),
            (reply(tenant_row(accountDomain="other.com")), "renewal_tenant_ambiguous"),
            (reply(tenant_row(accountName="royal reesink")), "renewal_tenant_ambiguous"),
            (reply(tenant_row(isDeleted=True)), "renewal_tenant_deleted"),
            (reply(tenant_row(id="short")), "renewal_row_ids_invalid"),
            (reply(tenant_row(license_expirationDate=None)), "renewal_row_unexpected"),
            (reply(tenant_row(license_expirationDate="2028-01-01T00:00:00Z")), "renewal_expiration_would_shorten"),
            (reply(status=401), "renewal_session_expired"),
            ({"status": 500}, "renewal_row_unavailable"),
        ]
        for value, code in cases:
            with self.subTest(code=code):
                self.assertEqual(renewal.before_edit(value, self.intended)[0], code)

    def test_already_current(self):
        row = tenant_row(scanningInterval="MONTHLY", license_expirationDate="2027-10-31T00:00:00Z")
        self.assertEqual(renewal.before_edit(reply(row), self.intended)[0], renewal.ALREADY_CURRENT)

    def test_epoch_dates_are_read(self):
        epoch = int(datetime(2027, 10, 31, tzinfo=timezone.utc).timestamp() * 1000)
        row = tenant_row(scanningInterval="Monthly", license_expirationDate=epoch)
        self.assertEqual(renewal.before_edit(reply(row), self.intended)[0], renewal.ALREADY_CURRENT)

    def test_after_edit_verified_only_with_both_values_and_nothing_else_changed(self):
        before = renewal.fingerprint(tenant_row())
        done = tenant_row(scanningInterval="MONTHLY", license_expirationDate="2027-10-31T00:00:00Z")
        self.assertEqual(renewal.after_edit(reply(done), self.intended, ACCOUNT_ID, before), renewal.VERIFIED)
        half = tenant_row(scanningInterval="MONTHLY")
        self.assertEqual(renewal.after_edit(reply(half), self.intended, ACCOUNT_ID, before), "renewal_values_not_kept")
        moved = dict(done, operatorAccounts=[])
        self.assertEqual(renewal.after_edit(reply(moved), self.intended, ACCOUNT_ID, before), "renewal_other_fields_changed")
        counts = tenant_row(scanningInterval="MONTHLY", license_expirationDate="2027-10-31T00:00:00Z", license_subDomainsNumber=1000)
        self.assertEqual(renewal.after_edit(reply(counts), self.intended, ACCOUNT_ID, before), "renewal_other_fields_changed")
        start = tenant_row(scanningInterval="MONTHLY", license_expirationDate="2027-10-31T00:00:00Z",
                           license_startDate="2026-11-03T00:00:00Z")
        self.assertEqual(renewal.after_edit(reply(start), self.intended, ACCOUNT_ID, before), "renewal_other_fields_changed")
        self.assertEqual(renewal.after_edit(reply(), self.intended, ACCOUNT_ID, before), "renewal_saved_unverified")
        self.assertEqual(renewal.after_edit(reply(status=403), self.intended, ACCOUNT_ID, before),
                         "renewal_session_expired_after_confirm")

    def test_count_differences_are_reported_not_applied(self):
        found = renewal.count_differences(tenant_row(), self.intended)
        self.assertEqual([item["field"] for item in found], ["Number of domains", "Number of subdomains"])
        self.assertEqual(found[1], {"field": "Number of subdomains", "tenant": 650, "licensed": 1000})

    def test_edit_response(self):
        self.assertIsNone(renewal.edit_response_problem("POST", ACCOUNT_ID, 200, ACCOUNT_ID))
        self.assertEqual(renewal.edit_response_problem("POST", "c" * 24, 200, ACCOUNT_ID), "renewal_save_id_mismatch")
        self.assertEqual(renewal.edit_response_problem("POST", ACCOUNT_ID, 500, ACCOUNT_ID), "renewal_save_failed")
        self.assertEqual(renewal.edit_response_problem("POST", ACCOUNT_ID, 401, ACCOUNT_ID),
                         "renewal_session_expired_after_confirm")


class FakeRoute:
    def __init__(self, method, url):
        self.request = SimpleNamespace(method=method, url=url, post_data=None)
        self.result = None

    def abort(self):
        self.result = "aborted"

    def continue_(self):
        self.result = "continued"


class GuardTests(unittest.TestCase):
    EDIT = "https://app.pentera.io/api/v1/backoffice/account/{}/edit"
    CREATE = "https://app.pentera.io" + live.CREATE_PATH

    def test_one_edit_for_the_armed_id_and_never_a_create(self):
        guard = live.RenewalEditGuard()
        guard.arm()
        create = FakeRoute("POST", self.CREATE)
        guard.handle(create)
        self.assertEqual(create.result, "aborted")
        early = FakeRoute("POST", self.EDIT.format(ACCOUNT_ID))
        guard.handle(early)
        self.assertEqual(early.result, "aborted")  # not armed yet
        guard.arm_edit(ACCOUNT_ID)
        other = FakeRoute("POST", self.EDIT.format("c" * 24))
        guard.handle(other)
        self.assertEqual(other.result, "aborted")
        first = FakeRoute("POST", self.EDIT.format(ACCOUNT_ID))
        guard.handle(first)
        self.assertEqual(first.result, "continued")
        again = FakeRoute("POST", self.EDIT.format(ACCOUNT_ID))
        guard.arm_edit(ACCOUNT_ID)
        guard.handle(again)
        self.assertEqual(again.result, "aborted")
        self.assertEqual(guard.edit_blocked, 3)

    def test_the_case3_guard_still_needs_a_create_first(self):
        guard = live.LiveRequestGuard()
        guard.arm_edit(ACCOUNT_ID)
        self.assertFalse(guard.edit_armed)


class IdsPlanTests(unittest.TestCase):
    def test_surface_renewal_route_plans_both_fields(self):
        row = dict(CO_ROW, Surface_Account_ID__c=None, Account_UUID__c=None)
        plan = ids_wb.plan("production", ids_wb.SURFACE_RENEWAL_ROUTE, row,
                           {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID}, "r", "r", True)
        self.assertEqual(plan.status, "ready_to_write")
        self.assertEqual(set(plan.values), {ids_wb.ID_FIELD, ids_wb.UUID_FIELD})

    def test_route_pairs_stay_exact(self):
        verified = {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID}
        case3_row = dict(CO_ROW, Onboarding_Product__c="Surface & Credential Exposure",
                         Onboarding_Type__c="New Product Onboarding", Surface_Account_ID__c=None, Account_UUID__c=None)
        self.assertEqual(ids_wb.plan("production", ids_wb.SURFACE_RENEWAL_ROUTE, case3_row, verified, "r", "r", True).reason,
                         "route_not_surface_renewal")
        renewal_row = dict(CO_ROW, Surface_Account_ID__c=None, Account_UUID__c=None)
        self.assertEqual(ids_wb.plan("production", ids_wb.CASE3_ROUTE, renewal_row, verified, "r", "r", True).reason,
                         "route_not_case3_new")


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.parent, True)
        self.row = dict(CO_ROW, Id="a0X000000000001AAA", Onboarding_Stage__c="Request Approved",
                        Surface_Account_ID__c=None, Account_UUID__c=None)
        self.calls = []
        locks = patch.multiple(readiness, PRODUCTION_RUNNER_SUPPORTED=True, PRODUCTION_DRY_RUN_SUPPORTED=True)
        locks.start()
        self.addCleanup(locks.stop)

    def run_argv(self, argv):
        self.calls.append(argv)
        values = argv[argv.index("--values") + 1]
        if "Onboarding_Stage__c" in values:
            self.row["Onboarding_Stage__c"] = values.split("=", 1)[1].strip("'")
        else:
            for pair in values.split(" "):
                key, value = pair.split("=", 1)
                self.row[key] = value
        return True

    def follow(self):
        return followup.after_verified_edit(self.parent, "CO-0829", {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID},
                                            datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc), lambda: dict(self.row),
                                            self.run_argv, "surface-onboarding")

    def lines(self):
        path = self.parent / "production-create-records" / followup.WRITES_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_ids_then_user_created(self):
        self.assertEqual(self.follow(), {"ids": "written_verified", "stage": "written_verified"})
        self.assertEqual(self.row["Onboarding_Stage__c"], "User Created")
        self.assertEqual(len(self.calls), 2)
        text = json.dumps(self.lines())
        self.assertNotIn(ACCOUNT_ID, text)
        self.assertNotIn(ACCOUNT_UUID, text)
        self.assertEqual([(line["step"], line["result"]) for line in self.lines()],
                         [("ids", "write_started"), ("ids", "written_verified"),
                          ("stage", "write_started"), ("stage", "written_verified")])

    def test_never_repeated_and_never_overwritten(self):
        self.follow()
        self.assertEqual(self.follow(), {"ids": "already_present", "stage": "already_present"})
        self.assertEqual(len(self.calls), 2)
        self.row["Surface_Account_ID__c"] = "c" * 24
        self.assertEqual(self.follow()["ids"], "different_value")

    def test_uncertain_ids_stop_the_stage(self):
        self.assertEqual(followup.after_verified_edit(
            self.parent, "CO-0829", {"account_id": ACCOUNT_ID, "account_uuid": ACCOUNT_UUID}, datetime.now(timezone.utc),
            lambda: dict(self.row), lambda argv: None, "surface-onboarding"),
            {"ids": "write_uncertain", "stage": "skipped_ids_not_verified"})
        self.assertEqual(self.follow()["ids"], "ids_write_needs_review")

    def test_stage_only_from_request_approved(self):
        self.row["Onboarding_Stage__c"] = "New"
        self.assertEqual(self.follow(), {"ids": "written_verified", "stage": "stage_not_expected"})

    def test_locked_constants_write_nothing(self):
        with patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False):
            self.assertEqual(self.follow()["ids"], "production_locked")
        self.assertEqual(self.calls, [])

    def test_card_states(self):
        self.assertEqual(followup.card_html(self.parent, "CO-0829"), "")
        followup.record(self.parent, "CO-0829", renewal.STEP, "write_started", datetime.now(timezone.utc))
        self.assertTrue(followup.edit_attempted(self.parent, "CO-0829"))
        self.assertIn("may or may not", followup.card_html(self.parent, "CO-0829"))
        followup.record(self.parent, "CO-0829", renewal.STEP, renewal.VERIFIED, datetime.now(timezone.utc))
        self.follow()
        card = followup.card_html(self.parent, "CO-0829")
        self.assertIn("read back", card)
        self.assertIn("stage User Created written", card)


if __name__ == "__main__":
    unittest.main()
