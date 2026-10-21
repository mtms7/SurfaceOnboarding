"""Case 3 (Surface & Credential Exposure, New) read-only production readback (owner step 3, 2026-10-21).

The same attended read-only run as the CE-only ``--production-readback`` (gate, request guard, zero clicks, redacted line), pointed at
the Case 3 route: the intended values come from the Case 3 fill plan, the row expectations are the Case 3 contract, and the tenant is
searched by an optional ``--tenant-name`` (the short name, docs/60) plus the exact main domain. Local only: fake page, fake
Salesforce source, no browser, no network.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from unittest import mock

from integration.onboarding import production_case3_contract as c3contract
from integration.onboarding import production_contract as contract
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_readback as rb
from integration.tests import test_production_contract as fx
import tools.attended_ce_only_playwright as runner

SHORT = "Example Klinik gemeinnuetzige TG"
SF_UUID = fx.UUID


def source(uuid=SF_UUID, account_id=""):
    return dataclasses.replace(c3.make_case3_source(), salesforce_account_uuid=uuid, salesforce_surface_account_id=account_id)


def case3_row(**over):
    row = fx.row_flat(**{
        "accountName": SHORT, "accountDomain": c3.MAIN, "accountCountryCode": "CL", "userEmailDomains": ["pentera.io"],
        "scanningInterval": "MONTHLY", "accountLicense.scanningFrequency": "MONTHLY",
        "leakedCredentialsScanningInterval": "WEEKLY", "accountLicense.assetsNumber": 10000,
        "accountLicense.domainsNumber": 10000, "accountLicense.subDomainsNumber": 500,
        "accountLicense.notificationsAllowed": True, "accountLicense.multipleUsersAllowed": True,
        "accountLicense.apiAccessAllowed": True, "subDomainsReconEnabled": True, "webDictionaryBruteForceEnabled": True})
    row.update(over)
    return row


def intended(**over):
    data = runner.case3_production_intended(c3.make_case3_source(), c3.RUN_DAY)
    data.update(over)
    return data


class ExpectedRowTests(unittest.TestCase):
    def test_a_row_that_matches_the_case_3_contract_has_no_mismatch(self):
        expected = c3contract.expected_case3_readback_row(intended(tenant_name=SHORT))
        self.assertEqual(contract.compare_readback(expected, case3_row()), ([], True))

    def test_the_manual_create_mistake_is_named_and_nothing_else(self):
        expected = c3contract.expected_case3_readback_row(intended(tenant_name=SHORT))
        mismatches, _ = contract.compare_readback(expected, case3_row(leakedCredentialsScanningInterval="NO_SCHEDULE"))
        self.assertEqual(mismatches, ["leakedCredentialsScanningInterval"])

    def test_ce_only_values_are_not_accepted_for_case_3(self):
        expected = c3contract.expected_case3_readback_row(intended(tenant_name=SHORT))
        ce_like = case3_row(**{"accountLicense.domainsNumber": 500, "accountLicense.notificationsAllowed": False,
                               "scanningInterval": "NO_SCHEDULE"})
        mismatches, _ = contract.compare_readback(expected, ce_like)
        self.assertEqual(mismatches, ["accountLicense.domainsNumber", "accountLicense.notificationsAllowed", "scanningInterval"])

    def test_the_tenant_name_may_be_the_short_name_but_not_a_ce_only_name(self):
        c3contract.expected_case3_readback_row(intended(tenant_name=SHORT))
        with self.assertRaises(ValueError):
            c3contract.expected_case3_readback_row(intended(tenant_name="Example - CE Only"))


class IntendedTests(unittest.TestCase):
    def test_the_intended_values_come_from_the_case_3_fill_plan(self):
        plan = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
        data = intended()
        self.assertEqual(data["tenant_name"], plan["texts"]["Company name"])
        self.assertEqual(data["primary_domain"], c3.MAIN)
        self.assertEqual(data["email_domain"], "pentera.io")
        self.assertEqual(data["license_end"], plan["license_end"])

    def test_the_source_uuid_is_kept_for_the_comparison_only(self):
        a, b = source(SF_UUID), source("")
        self.assertEqual(a, b)
        self.assertNotIn(SF_UUID, repr(a))
        self.assertEqual(a.salesforce_account_uuid, SF_UUID)


class RunTests(unittest.TestCase):
    sf_account_id = ""

    def run_case3(self, reply=None, tenant_name=None, loader=None, **page_kw):
        h = rb.Harness(self, **page_kw)
        if reply is not None:
            h.page.reply = reply

        def load(_reference):
            return source(account_id=self.sf_account_id)

        h.records = h.tmp / "records"
        with mock.patch("builtins.print"):
            line = runner.run_production_readback(
                "CO-0800", environ=dict(rb.GOOD_ENV), now=rb.NOW, report_base=h.tmp, load_source=loader or load,
                open_page=h.open_page, route=runner.CASE3_ENGINE, tenant_name=tenant_name, records_parent=h.records)
        return h, json.loads(line), line

    def records(self, h):
        folder = h.records / "production-create-records"
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("CO-0800-*.json"))] if folder.exists() else []

    def test_a_matching_case_3_tenant_is_compared_with_zero_clicks_and_no_write(self):
        h, line, text = self.run_case3(reply=rb.good_reply([case3_row()]), tenant_name=SHORT)
        self.assertEqual((line["result"], line["tenant_found"], line["mismatches"], line["uuid_ok"], line["salesforce_uuid_matches"]),
                         ("production_readback_complete", True, [], True, True))
        self.assertEqual((h.page.clicks, h.page.typed), ([], []))
        self.assertEqual((line["production_write"], line["salesforce_writeback"]), ("not_performed", "not_performed"))
        self.assertEqual(h.page.search_argument, SHORT)  # the page searched the short name
        for secret in (SF_UUID, SHORT, c3.MAIN):
            self.assertNotIn(secret, text)

    def test_the_default_search_is_the_plan_name_and_a_wrong_row_is_not_found(self):
        h, line, _ = self.run_case3(reply=rb.good_reply([case3_row()]))
        self.assertEqual(h.page.search_argument, c3.NAME)
        self.assertEqual((line["result"], line["reason"]), ("production_readback_refused", "tenant_not_found"))

    def test_a_drifted_row_names_only_the_field(self):
        _h, line, _ = self.run_case3(reply=rb.good_reply([case3_row(leakedCredentialsScanningInterval="NO_SCHEDULE")]),
                                     tenant_name=SHORT)
        self.assertEqual((line["result"], line["mismatches"]), ("production_readback_complete", ["leakedCredentialsScanningInterval"]))

    def test_a_different_salesforce_uuid_is_reported_as_a_flag_only(self):
        _h, line, text = self.run_case3(reply=rb.good_reply([case3_row(accountUuid=fx.OTHER_UUID)]), tenant_name=SHORT)
        self.assertIs(line["salesforce_uuid_matches"], False)
        self.assertNotIn(fx.OTHER_UUID, text)

    def test_a_route_mismatch_has_its_own_code_and_opens_no_browser(self):
        def mismatch(_ref):
            raise runner.SurfaceSourceError("case3_route_mismatch")

        h, line, _ = self.run_case3(loader=mismatch)
        self.assertEqual(line["reason"], "route_not_case3_new")
        self.assertEqual(h.opened, 0)

    def test_other_source_errors_are_a_salesforce_failure(self):
        def boom(_ref):
            raise runner.SurfaceSourceError("case3_source_unavailable")

        h, line, _ = self.run_case3(loader=boom)
        self.assertEqual(line["reason"], "salesforce_read_failed")
        self.assertEqual(h.opened, 0)

    def test_an_unsupported_route_is_refused_before_anything(self):
        h = rb.Harness(self)
        with mock.patch("builtins.print"):
            line = json.loads(runner.run_production_readback("CO-0800", environ=dict(rb.GOOD_ENV), now=rb.NOW, report_base=h.tmp,
                                                             open_page=h.open_page, route="case_5_renew_ce_new_surface"))
        self.assertEqual(line["reason"], "route_not_supported")
        self.assertEqual(h.opened, 0)

    def test_a_write_attempt_is_blocked_and_reported(self):
        _h, line, _ = self.run_case3(reply=rb.good_reply([case3_row()]), tenant_name=SHORT, attempt_write=True)
        self.assertEqual(line["reason"], "write_request_blocked")


class VerifiedRecordTests(RunTests):
    """A fully matching Case 3 readback leaves ONE ``readback_verified`` record with the ids (owner request 2026-10-21); nothing else does."""

    def test_a_fully_matching_readback_records_the_verified_tenant_with_its_ids(self):
        h, line, text = self.run_case3(reply=rb.good_reply([case3_row()]), tenant_name=SHORT)
        self.assertEqual(line["mismatches"], [])
        found = self.records(h)
        self.assertEqual(len(found), 1)
        record = found[0]
        self.assertEqual((record["outcome"], record["route"], record["tenant_name"]), ("readback_verified", runner.CASE3_ENGINE, SHORT))
        self.assertEqual((record["account_id"], record["account_uuid"]), (fx.ROW_ID, SF_UUID))
        self.assertEqual(record["domains"], [c3.MAIN])
        for secret in (fx.ROW_ID, SF_UUID, SHORT):
            self.assertNotIn(secret, text)  # the printed line still carries no identifier
        self.assertNotIn(fx.ROW_ID, h.report()[1])
        self.assertNotIn(SF_UUID, h.report()[1])

    def test_the_salesforce_surface_account_id_is_compared_with_the_tenant_id_by_flag_only(self):
        for sf_value, want, recorded in ((fx.ROW_ID, True, 1), (fx.ROW_ID.upper(), True, 1), ("", None, 1),
                                         ("ffffffffffffffffffffffff", False, 0)):
            with self.subTest(sf_value=sf_value):
                self.sf_account_id = sf_value
                h, line, text = self.run_case3(reply=rb.good_reply([case3_row()]), tenant_name=SHORT)
                self.assertIs(line["salesforce_account_id_matches"], want)
                self.assertEqual(len(self.records(h)), recorded)
                self.assertNotIn(fx.ROW_ID, text)
                self.assertNotIn("ffffffffffffffffffffffff", text)

    def test_the_ce_only_line_has_no_account_id_key(self):
        h = rb.Harness(self)
        with mock.patch("builtins.print"):
            line = json.loads(runner.run_production_readback("CO-0797", environ=dict(rb.GOOD_ENV), now=rb.NOW, report_base=h.tmp,
                                                             load_source=lambda _r: rb.make_source(), open_page=h.open_page))
        self.assertNotIn("salesforce_account_id_matches", line)

    def test_nothing_is_recorded_unless_everything_matches(self):
        cases = (("mismatch", case3_row(leakedCredentialsScanningInterval="NO_SCHEDULE")),
                 ("other_uuid", case3_row(accountUuid=fx.OTHER_UUID)),
                 ("bad_uuid", case3_row(accountUuid="not-a-uuid")))
        for label, row in cases:
            with self.subTest(label):
                h, _line, _ = self.run_case3(reply=rb.good_reply([row]), tenant_name=SHORT)
                self.assertEqual(self.records(h), [], label)
        h, _line, _ = self.run_case3(reply=rb.good_reply([case3_row()]))  # default name: tenant not found
        self.assertEqual(self.records(h), [])

    def test_a_record_that_cannot_be_written_never_changes_the_outcome(self):
        h = rb.Harness(self)
        h.page.reply = rb.good_reply([case3_row()])
        blocker = h.tmp / "not-a-folder"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch("builtins.print"):
            line = json.loads(runner.run_production_readback(
                "CO-0800", environ=dict(rb.GOOD_ENV), now=rb.NOW, report_base=h.tmp, load_source=lambda _r: source(),
                open_page=h.open_page, route=runner.CASE3_ENGINE, tenant_name=SHORT, records_parent=blocker))
        self.assertEqual((line["result"], line["mismatches"]), ("production_readback_complete", []))

    def test_the_ce_only_readback_writes_no_record(self):
        h = rb.Harness(self)
        records = h.tmp / "records"
        with mock.patch("builtins.print"):
            runner.run_production_readback("CO-0797", environ=dict(rb.GOOD_ENV), now=rb.NOW, report_base=h.tmp,
                                           load_source=lambda _r: rb.make_source(), open_page=h.open_page, records_parent=records)
        self.assertFalse((records / "production-create-records").exists())


class CommandLineTests(unittest.TestCase):
    def main_with(self, *argv):
        with mock.patch.object(sys, "argv", ["runner", *argv]), \
                mock.patch.object(runner, "run_production_readback", return_value="{}") as run, mock.patch("builtins.print"):
            code = runner.main()
        return code, run

    def test_the_case_3_readback_passes_the_route_and_the_short_name(self):
        code, run = self.main_with("--production-readback", "--co", "CO-0800", "--route", runner.CASE3_ENGINE,
                                   "--tenant-name", SHORT)
        self.assertEqual(code, 0)
        run.assert_called_once_with("CO-0800", route=runner.CASE3_ENGINE, tenant_name=SHORT)

    def test_the_ce_only_default_is_unchanged(self):
        _code, run = self.main_with("--production-readback", "--co", "CO-0797")
        run.assert_called_once_with("CO-0797", route=runner.CE_ENGINE, tenant_name=None)

    def test_the_readback_stays_a_separate_read_only_run(self):
        with self.assertRaises(SystemExit):
            self.main_with("--production-readback", "--co", "CO-0800", "--revision", "x")


if __name__ == "__main__":
    unittest.main()
