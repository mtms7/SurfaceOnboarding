from __future__ import annotations

import dataclasses
import io
import json
import os
import shutil
import sys
import tempfile
import contextlib
import types
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
from tools.attended_ce_only_playwright import (
    READBACK_SOURCE,
    READBACK_STATE,
    READBACK_STATES,
    RunnerStateUnavailable,
    _classify_leonardo_session,
    _exact_tenant_rows,
    _format_date_for_placeholder,
    _is_tenant_management_url,
    _locate_confirm_button,
    _locate_form_field,
    _readback_details,
    bootstrap_leonardo_session,
    build_ce_only_fill,
    ce_fill_source,
    ce_license_dates,
    ce_only_names,
    check_leonardo_session,
    leonardo_profile,
    load_runner_state,
    one_email_domain,
    record_runner_result,
    record_runner_start,
    reset_leonardo_profile,
    reset_runner_record,
    run_readback,
    select_ce_subscription,
    source_for_fill,
    write_readback_evidence,
)

REVISION = "2026-09-17T10:00:00Z"
ACCOUNT = "Sample Company"
MAIN_DOMAIN = "company.example"
EMAIL_DOMAIN = "company.example"
TENANT = "Sample Company - CE Only"
SURFACE_ID = "a123456789012345"
UUID = "b" * 32


class RunnerStateFileTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_runner_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def test_load_absent_state_file_returns_empty(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            self.assertEqual(load_runner_state(), {})

    def test_record_start_round_trip(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            record_runner_start("CO-0702", REVISION, "2026-09-21T10:00:00")
            state = load_runner_state()
            self.assertEqual(state["CO-0702"]["source_revision"], REVISION)
            self.assertEqual(state["CO-0702"]["started_on"], "2026-09-21T10:00:00")
            self.assertNotIn("result", state["CO-0702"])

    def test_record_start_new_revision_supersedes_prior_record(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            record_runner_start("CO-0702", "rev1", "2026-09-21T10:00:00")
            record_runner_start("CO-0702", "rev2", "2026-09-21T11:00:00")
            state = load_runner_state()
            self.assertEqual(state["CO-0702"]["source_revision"], "rev2")
            self.assertEqual(state["CO-0702"]["started_on"], "2026-09-21T11:00:00")

    def test_record_start_rejects_invalid_reference(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            with self.assertRaises(ValueError):
                record_runner_start("BAD-REF", REVISION, "2026-09-21T10:00:00")

    def test_record_result_first_writer_wins(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            record_runner_start("CO-0702", REVISION, "2026-09-21T10:00:00")
            record_runner_result("CO-0702", REVISION, "readback_verified", "2026-09-21T10:05:00")
            record_runner_result("CO-0702", REVISION, "operator_cancelled_no_create", "2026-09-21T10:06:00")
            state = load_runner_state()
            self.assertEqual(state["CO-0702"]["result"], "readback_verified")
            self.assertEqual(state["CO-0702"]["completed_on"], "2026-09-21T10:05:00")

    def test_record_result_for_other_revision_is_ignored(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            record_runner_start("CO-0702", REVISION, "2026-09-21T10:00:00")
            record_runner_result("CO-0702", "other-revision", "readback_verified", "2026-09-21T10:05:00")
            state = load_runner_state()
            self.assertEqual(state["CO-0702"]["source_revision"], REVISION)
            self.assertNotIn("result", state["CO-0702"])

    def test_record_result_creates_a_missing_record(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            record_runner_result("CO-0702", REVISION, "readback_verified", "2026-09-21T10:05:00")
            state = load_runner_state()
            self.assertEqual(state["CO-0702"]["source_revision"], REVISION)
            self.assertEqual(state["CO-0702"]["result"], "readback_verified")
            self.assertEqual(state["CO-0702"]["completed_on"], "2026-09-21T10:05:00")

    def test_load_corrupt_state_file_fails_closed(self):
        path = self._temp_path("state.json")
        path.write_text("{ not json", encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(RunnerStateUnavailable):
                load_runner_state()

    def test_load_rejects_unknown_record_key(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "bogus": "x"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(RunnerStateUnavailable):
                load_runner_state()

    def test_load_rejects_completed_without_result(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "completed_on": "2026-09-21T10:05:00"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(RunnerStateUnavailable):
                load_runner_state()

    def test_load_rejects_non_iso_timestamp(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "started_on": "not-a-date"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(RunnerStateUnavailable):
                load_runner_state()

    def test_load_rejects_non_dict_top_level(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps([{"CO-0702": {"source_revision": REVISION}}]), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(RunnerStateUnavailable):
                load_runner_state()


class ReadbackEvidenceTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_readback_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def test_write_produces_exact_schema(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            write_readback_evidence("CO-0702", SURFACE_ID, UUID, "2026-09-21")
            raw = json.loads(runner.READBACK_PATH.read_text(encoding="utf-8"))
            self.assertEqual(raw["CO-0702"], {
                "surface_account_id": SURFACE_ID,
                "account_uuid": UUID,
                "leonardo_state": READBACK_STATE,
                "observed_on": "2026-09-21",
                "source": READBACK_SOURCE,
            })

    def test_write_preserves_other_cos(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            write_readback_evidence("CO-0701", "c" * 16, "d" * 32, "2026-09-20")
            write_readback_evidence("CO-0702", SURFACE_ID, UUID, "2026-09-21")
            raw = json.loads(runner.READBACK_PATH.read_text(encoding="utf-8"))
            self.assertIn("CO-0701", raw)
            self.assertEqual(raw["CO-0702"]["surface_account_id"], SURFACE_ID)

    def test_write_rejects_invalid_surface_account_id(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            with self.assertRaises(ValueError):
                write_readback_evidence("CO-0702", "short", UUID, "2026-09-21")

    def test_write_rejects_invalid_account_uuid(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            with self.assertRaises(ValueError):
                write_readback_evidence("CO-0702", SURFACE_ID, "not-hex-uuid", "2026-09-21")

    def test_write_rejects_invalid_observed_date(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            with self.assertRaises(ValueError):
                write_readback_evidence("CO-0702", SURFACE_ID, UUID, "not-a-date")

    def test_write_rejects_invalid_reference(self):
        with patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")):
            with self.assertRaises(ValueError):
                write_readback_evidence("BAD-REF", SURFACE_ID, UUID, "2026-09-21")

    def test_write_refuses_corrupt_existing_evidence(self):
        path = self._temp_path("readbacks.json")
        path.write_text("{ corrupt", encoding="utf-8")
        with patch.object(runner, "READBACK_PATH", path):
            with self.assertRaises(ValueError):
                write_readback_evidence("CO-0702", SURFACE_ID, UUID, "2026-09-21")


class CeOnlyNamingTests(unittest.TestCase):
    def test_tenant_name_uses_regular_hyphen_and_account_name(self):
        self.assertEqual(ce_only_names("Sample Company").tenant_name, "Sample Company - CE Only")

    def test_trailing_period_is_dropped_from_the_tenant_name(self):
        # Owner decision 2026-09-29: "Tango Group Ltd." -> "Tango Group Ltd - CE Only".
        names = ce_only_names("Tango Group Ltd.")
        self.assertEqual(names.tenant_name, "Tango Group Ltd - CE Only")
        self.assertEqual(names.primary_user_alias, "tgl")

    def test_only_trailing_periods_and_spaces_are_dropped(self):
        for account, expected in (
            ("Acme Inc..", "Acme Inc - CE Only"),
            ("  Acme Inc. ", "Acme Inc - CE Only"),
            ("A.B. Holdings", "A.B. Holdings - CE Only"),
            ("Acme Inc", "Acme Inc - CE Only"),
        ):
            with self.subTest(account=account):
                self.assertEqual(ce_only_names(account).tenant_name, expected)

    def test_account_name_of_only_periods_raises(self):
        with self.assertRaises(ValueError):
            ce_only_names("...")

    def test_alias_compacts_names_up_to_fifteen_chars(self):
        # The alias is casefolded (lowercase) in both branches: the
        # organization email alias must match the lowercase contract.
        name = "Abcdefghijklmno"
        self.assertEqual(len(name), 15)
        self.assertEqual(ce_only_names(name).primary_user_alias, "abcdefghijklmno")

    def test_alias_uses_word_initials_beyond_fifteen_chars(self):
        name = "Abcdefghijklmnop"
        self.assertEqual(len(name), 16)
        self.assertEqual(ce_only_names(name).primary_user_alias, "a")
        self.assertEqual(ce_only_names("First Main Bank & Trust").primary_user_alias, "fmbt")

    def test_blank_account_name_raises(self):
        with self.assertRaises(ValueError):
            ce_only_names("   ")

    def test_account_name_without_alphanumerics_raises(self):
        with self.assertRaises(ValueError):
            ce_only_names("&&&")


class OneEmailDomainTests(unittest.TestCase):
    def test_single_valid_domain_is_casefolded(self):
        self.assertEqual(one_email_domain("Example.Test"), "example.test")

    def test_surrounding_separators_are_tolerated(self):
        self.assertEqual(one_email_domain("  example.test  "), "example.test")
        self.assertEqual(one_email_domain("example.test;"), "example.test")

    def test_multiple_or_missing_domains_rejected(self):
        self.assertIsNone(one_email_domain("one.test,two.test"))
        self.assertIsNone(one_email_domain(""))

    def test_malformed_domains_rejected(self):
        self.assertIsNone(one_email_domain("nodot"))
        self.assertIsNone(one_email_domain("-leading.test"))
        self.assertIsNone(one_email_domain("trailing-.test"))
        self.assertIsNone(one_email_domain("example.test."))

    def test_non_string_rejected(self):
        self.assertIsNone(one_email_domain(None))
        self.assertIsNone(one_email_domain(123))


class SourceForFillTests(unittest.TestCase):
    @staticmethod
    def _completed(stdout: str, returncode: int = 0):
        completed = unittest.mock.Mock()
        completed.stdout = stdout
        completed.returncode = returncode
        return completed

    @staticmethod
    def _sf_json(records, status: int = 0) -> str:
        return json.dumps({"status": status, "result": {"records": records}})

    def test_invalid_reference_fails_before_any_read(self):
        with self.assertRaisesRegex(RuntimeError, "invalid_co_reference"):
            source_for_fill("BAD")

    def test_nonzero_status_fails_closed(self):
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json([{"Name": "CO-0702"}], status=1))):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                source_for_fill("CO-0702")

    def test_two_rows_fail_closed(self):
        records = [{"Name": "CO-0702"}, {"Name": "CO-0702"}]
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json(records))):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                source_for_fill("CO-0702")

    def test_row_name_mismatch_fails_closed(self):
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json([{"Name": "CO-9999"}]))):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                source_for_fill("CO-0702")

    def test_missing_email_domain_fails_closed(self):
        records = [{"Name": "CO-0702", "LastModifiedDate": REVISION, "Account_Name__c": ACCOUNT}]
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json(records))):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                source_for_fill("CO-0702")

    def test_none_stdout_fails_closed(self):
        # A failed subprocess decode (e.g. cp1252 on a UTF-8 byte) leaves
        # stdout as None; the read must fail closed, not raise TypeError.
        with patch.object(runner.subprocess, "run", return_value=self._completed(None)):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                source_for_fill("CO-0702")

    def test_valid_read_returns_normalized_fill_values(self):
        records = [{"Name": "CO-0702", "LastModifiedDate": REVISION, "Account_Name__c": ACCOUNT,
                    "Email_Domains__c": "Company.Example"}]
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json(records))):
            revision, account, main_domain, email_domain, tenant = source_for_fill("CO-0702")
        self.assertEqual(revision, REVISION)
        self.assertEqual(account, ACCOUNT)
        # CE-only: the Primary Domain is the single Email Domains value.
        self.assertEqual(main_domain, "company.example")
        self.assertEqual(email_domain, "company.example")
        self.assertEqual(tenant, TENANT)

    def test_main_domain_field_is_ignored_for_ce_only(self):
        # A populated Main_Domain__c must not affect the CE-only fill values.
        records = [{"Name": "CO-0702", "LastModifiedDate": REVISION, "Account_Name__c": ACCOUNT,
                    "Main_Domain__c": "other.example", "Email_Domains__c": "company.example"}]
        with patch.object(runner.subprocess, "run", return_value=self._completed(self._sf_json(records))):
            revision, account, main_domain, email_domain, tenant = source_for_fill("CO-0702")
        self.assertEqual(main_domain, "company.example")
        self.assertEqual(email_domain, "company.example")


class SelectCeSubscriptionTests(unittest.TestCase):
    @staticmethod
    def _row(product: str, start: str, end: str) -> dict:
        return {"Product_Full_Name__c": product,
                "DealHub_Subscription_Start_Date__c": start,
                "DealHub_Subscription_End_Date__c": end}

    def test_selects_core_plus_commercial_row(self):
        rows = [self._row("Pentera Core Plus Commercial - 500 End Points", "2026-10-24", "2029-10-23")]
        self.assertEqual(select_ce_subscription(rows), (date(2026, 10, 24), date(2029, 10, 23)))

    def test_prefix_match_is_case_insensitive(self):
        rows = [self._row("pentera core plus commercial - 500 End Points", "2026-10-01", "2027-09-30")]
        self.assertEqual(select_ce_subscription(rows), (date(2026, 10, 1), date(2027, 9, 30)))

    def test_bulk_rows_never_match(self):
        # A Bulk row whose name starts with the Core Plus prefix must still be
        # excluded: it never carries the CE license.
        rows = [self._row("Pentera Core Plus Commercial - Bulk 1000", "2026-10-24", "2029-10-23")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            select_ce_subscription(rows)

    def test_additional_rows_never_match(self):
        rows = [self._row("Pentera Core Plus Commercial - Additional Seats", "2026-10-24", "2029-10-23")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            select_ce_subscription(rows)

    def test_non_matching_products_are_ignored(self):
        rows = [self._row("Pentera Enterprise", "2026-10-24", "2029-10-23")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            select_ce_subscription(rows)

    def test_no_rows_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            select_ce_subscription([])

    def test_unparseable_dates_fail_closed(self):
        rows = [self._row("Pentera Core Plus Commercial - 500", "not-a-date", "2029-10-23")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
            select_ce_subscription(rows)

    def test_end_before_start_fails_closed(self):
        rows = [self._row("Pentera Core Plus Commercial - 500", "2029-10-23", "2026-10-24")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
            select_ce_subscription(rows)

    def test_conflicting_matching_rows_fail_closed(self):
        rows = [self._row("Pentera Core Plus Commercial - 500", "2026-10-24", "2029-10-23"),
                self._row("Pentera Core Plus Commercial - 500 (Renewal)", "2027-01-01", "2030-01-01")]
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
            select_ce_subscription(rows)

    def test_agreeing_matching_rows_succeed(self):
        rows = [self._row("Pentera Core Plus Commercial - 500", "2026-10-24", "2029-10-23"),
                self._row("Pentera Core Plus Commercial - 500 (Renewal)", "2026-10-24", "2029-10-23")]
        self.assertEqual(select_ce_subscription(rows), (date(2026, 10, 24), date(2029, 10, 23)))


class CeLicenseDatesTests(unittest.TestCase):
    def test_one_year_minus_one_day(self):
        # Tango: 2026-10-24 -> 2029-10-23 gives CE 2026-10-24 -> 2027-10-23.
        self.assertEqual(ce_license_dates(date(2026, 10, 24), date(2029, 10, 23)),
                         (date(2026, 10, 24), date(2027, 10, 23)))

    def test_capped_at_subscription_end(self):
        self.assertEqual(ce_license_dates(date(2026, 10, 24), date(2026, 12, 31)),
                         (date(2026, 10, 24), date(2026, 12, 31)))

    def test_subscription_end_exactly_at_annual_end(self):
        self.assertEqual(ce_license_dates(date(2026, 10, 24), date(2027, 10, 23)),
                         (date(2026, 10, 24), date(2027, 10, 23)))

    def test_february_29_start_clamps_to_february_28(self):
        # 2028-02-29 + 1 year is 2029-02-28; minus one day is 2029-02-27.
        self.assertEqual(ce_license_dates(date(2028, 2, 29), date(2031, 2, 28)),
                         (date(2028, 2, 29), date(2029, 2, 27)))


class CeFillSourceTests(unittest.TestCase):
    @staticmethod
    def _completed(stdout: str, returncode: int = 0):
        completed = unittest.mock.Mock()
        completed.stdout = stdout
        completed.returncode = returncode
        return completed

    @staticmethod
    def _sf_json(records, status: int = 0) -> str:
        return json.dumps({"status": status, "result": {"records": records}})

    CO_RECORD = {
        "Name": "CO-0702", "LastModifiedDate": REVISION,
        "Account__c": "a123456789012345", "Account_Name__c": ACCOUNT,
        "Email_Domains__c": "Company.Example", "Account_Country__c": "France", "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
    }
    SUBSCRIPTION_RECORD = {
        "Product_Full_Name__c": "Pentera Core Plus Commercial - 500 End Points",
        "DealHub_Subscription_Start_Date__c": "2026-09-28",
        "DealHub_Subscription_End_Date__c": "2029-09-27",
    }

    def _patch_sf(self, co_records, subscription_records):
        return patch.object(runner.subprocess, "run", side_effect=[
            self._completed(self._sf_json(co_records)),
            self._completed(self._sf_json(subscription_records)),
        ])

    def test_invalid_reference_fails_before_any_read(self):
        with patch.object(runner.subprocess, "run") as run_mock:
            with self.assertRaisesRegex(RuntimeError, "invalid_co_reference"):
                ce_fill_source("BAD")
        run_mock.assert_not_called()

    def test_non_ce_route_fails_closed_before_the_subscription_read(self):
        for product, onboarding_type in (("Surface", "New Product Onboarding"),
                                         ("Surface & Credential Exposure", "New Product Onboarding"),
                                         ("Credential Exposure", "Renewal"), (None, "New Product Onboarding")):
            record = dict(self.CO_RECORD, Onboarding_Product__c=product, Onboarding_Type__c=onboarding_type)
            with self.subTest(product=product, type=onboarding_type), \
                    patch.object(runner.subprocess, "run",
                                 side_effect=[self._completed(self._sf_json([record]))]) as run_mock:
                with self.assertRaisesRegex(RuntimeError, "ce_route_mismatch"):
                    ce_fill_source("CO-0702")
                self.assertEqual(run_mock.call_count, 1)

    def test_valid_read_returns_full_source(self):
        with self._patch_sf([self.CO_RECORD], [self.SUBSCRIPTION_RECORD]):
            source = ce_fill_source("CO-0702")
        self.assertEqual(source.reference, "CO-0702")
        self.assertEqual(source.source_revision, REVISION)
        self.assertEqual(source.account_id, "a123456789012345")
        self.assertEqual(source.account_name, ACCOUNT)
        self.assertEqual(source.email_domain, "company.example")
        self.assertEqual(source.country, "France")
        self.assertEqual(source.subscription_start, date(2026, 9, 28))
        self.assertEqual(source.subscription_end, date(2029, 9, 27))
        self.assertEqual(source.tenant_name, TENANT)
        self.assertEqual(source.primary_user_alias, "samplecompany")

    def test_salesforce_account_uuid_sets_the_pre_create_flag(self):
        with self._patch_sf([self.CO_RECORD], [self.SUBSCRIPTION_RECORD]):
            self.assertFalse(ce_fill_source("CO-0702").salesforce_id_present)
        with self._patch_sf([dict(self.CO_RECORD, Account_UUID__c="b" * 32)], [self.SUBSCRIPTION_RECORD]):
            self.assertTrue(ce_fill_source("CO-0702").salesforce_id_present)
        with self._patch_sf([dict(self.CO_RECORD, Account_UUID__c="  ")], [self.SUBSCRIPTION_RECORD]):
            self.assertFalse(ce_fill_source("CO-0702").salesforce_id_present)

    def test_two_co_rows_fail_closed(self):
        with self._patch_sf([self.CO_RECORD, self.CO_RECORD], [self.SUBSCRIPTION_RECORD]):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                ce_fill_source("CO-0702")

    def test_missing_country_fails_closed(self):
        record = dict(self.CO_RECORD)
        record["Account_Country__c"] = None
        with self._patch_sf([record], [self.SUBSCRIPTION_RECORD]):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                ce_fill_source("CO-0702")

    def test_missing_email_domain_fails_closed(self):
        record = dict(self.CO_RECORD)
        record["Email_Domains__c"] = ""
        with self._patch_sf([record], [self.SUBSCRIPTION_RECORD]):
            with self.assertRaisesRegex(RuntimeError, "salesforce_fill_source_unavailable"):
                ce_fill_source("CO-0702")

    def test_missing_subscription_fails_closed(self):
        with self._patch_sf([self.CO_RECORD], []):
            with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
                ce_fill_source("CO-0702")

    def test_ambiguous_subscription_propagates_its_own_code(self):
        with self._patch_sf([self.CO_RECORD],
                            [self._row("Pentera Core Plus Commercial - 500", "x", "y")]):
            with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
                ce_fill_source("CO-0702")

    @staticmethod
    def _row(product: str, start: str, end: str) -> dict:
        return {"Product_Full_Name__c": product,
                "DealHub_Subscription_Start_Date__c": start,
                "DealHub_Subscription_End_Date__c": end}


class BuildCeOnlyFillTests(unittest.TestCase):
    def _source(self, account_name: str = ACCOUNT, country: str = "France") -> runner.CeFillSource:
        names = ce_only_names(account_name)
        return runner.CeFillSource(
            reference="CO-0702", source_revision=REVISION, account_id="a123456789012345",
            account_name=account_name, email_domain="company.example", country=country,
            subscription_start=date(2026, 9, 28), subscription_end=date(2029, 9, 27),
            tenant_name=names.tenant_name, primary_user_alias=names.primary_user_alias,
        )

    def test_text_controls_match_the_confirmed_contract(self):
        plan = build_ce_only_fill(self._source())
        self.assertEqual(plan["texts"]["Company name"], TENANT)
        self.assertEqual(plan["texts"]["Company primary domain"], "company.example")
        self.assertEqual(plan["texts"]["User email domains  (Comma Separated Values)"], "pentera.io")
        self.assertEqual(plan["texts"]["First name"], "Milton")
        self.assertEqual(plan["texts"]["Last name"], "Stevenson")
        self.assertEqual(plan["texts"]["Organization Email"], "milton.stevenson+samplecompany@pentera.io")
        self.assertEqual(plan["texts"]["Leaked Credentials scanned domains (Comma Separated Values)"],
                         "company.example")
        self.assertEqual(plan["texts"]["Number of assets"], "1")
        self.assertEqual(plan["texts"]["Number of domains"], "1")
        self.assertEqual(plan["texts"]["Number of subdomains"], "1")

    def test_selects_match_the_confirmed_contract(self):
        plan = build_ce_only_fill(self._source())
        self.assertEqual(plan["selects"], {
            "Account Type": "Customer",
            "Country": "France",
            "Scanning interval": "None",
            "Leaked Credentials scanning interval": "Weekly",
            "Type": "Prepaid annual subscription",
        })

    def test_checkboxes_match_the_confirmed_toggle_contract(self):
        # Tenant MFA required (owner decision 2026-09-29); every feature toggle
        # off except Leaked Credentials, Provisioning, Subdomains.
        plan = build_ce_only_fill(self._source())
        self.assertEqual(plan["checkboxes"], {
            "mfaRequired": True,
            "scan_now": False,
            "notificationsAllowed": False,
            "multipleUsersAllowed": False,
            "apiAccessAllowed": False,
            "phishingEnabled": False,
            "leakedCredentialsAllowed": True,
            "provisioningEnabled": True,
            "subDomainsNumberAllowed": True,
            # Advanced options: Automated discovery, Recon Subdomains, and Web
            # dictionary brute force default ON and must be OFF (2026-09-29);
            # the remaining advanced toggles are pinned OFF.
            "automatedDiscoveryEnabled": False,
            "subDomainsReconEnabled": False,
            "webDictionaryBruteForceEnabled": False,
            "webDorkingEnabled": False,
            "fullNucleiScanEnabled": False,
            "authenticatedTestingEnabled": False,
            "staticOutboundIpEnabled": False,
            "aiEnabled": False,
            "multipleAttackStacksEnabled": False,
        })

    def test_requested_advanced_toggles_are_off(self):
        checkboxes = build_ce_only_fill(self._source())["checkboxes"]
        for key in ("automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled"):
            self.assertIs(checkboxes[key], False, key)

    def test_license_dates_use_the_ce_rule(self):
        # Start = the run day (Leonardo Dev refuses a future start, owner
        # decision 2026-09-29); expiration keeps the contract rule.
        plan = build_ce_only_fill(self._source(), date(2026, 9, 29))
        self.assertEqual(plan["license_start"], date(2026, 9, 29))
        self.assertEqual(plan["license_end"], date(2027, 9, 27))

    def test_future_contract_start_uses_run_day_and_contract_expiration(self):
        # CO-0679 shape: contract starts 2026-10-24, run happens 2026-09-29.
        self.assertEqual(runner.ce_run_license_dates(date(2026, 10, 24), date(2029, 10, 23), date(2026, 9, 29)),
                         (date(2026, 9, 29), date(2027, 10, 23)))

    def test_expired_or_same_day_expiration_fails_closed(self):
        for run_day in (date(2027, 9, 27), date(2027, 10, 1)):
            with self.subTest(run_day=run_day), self.assertRaises(ValueError):
                runner.ce_run_license_dates(date(2026, 9, 28), date(2029, 9, 27), run_day)

    def test_run_day_defaults_to_today(self):
        with patch.object(runner, "_run_day", return_value=date(2026, 10, 2)):
            self.assertEqual(build_ce_only_fill(self._source())["license_start"], date(2026, 10, 2))

    def test_organization_email_uses_lowercase_alias(self):
        plan = build_ce_only_fill(self._source(account_name="Abcdefghijklmno"))
        self.assertEqual(plan["texts"]["Organization Email"],
                         "milton.stevenson+abcdefghijklmno@pentera.io")

    def test_long_account_name_uses_lowercase_initials(self):
        plan = build_ce_only_fill(self._source(account_name="First Main Bank & Trust"))
        self.assertEqual(plan["texts"]["Organization Email"],
                         "milton.stevenson+fmbt@pentera.io")


class DateFormatTests(unittest.TestCase):
    VALUE = date(2026, 10, 24)

    def test_mm_dd_yyyy(self):
        self.assertEqual(_format_date_for_placeholder(self.VALUE, "mm/dd/yyyy"), "10/24/2026")

    def test_yyyy_mm_dd(self):
        self.assertEqual(_format_date_for_placeholder(self.VALUE, "yyyy-mm-dd"), "2026-10-24")

    def test_single_letter_tokens(self):
        self.assertEqual(_format_date_for_placeholder(self.VALUE, "m/d/yy"), "10/24/26")

    def test_missing_placeholder_falls_back_to_mm_dd_yyyy(self):
        self.assertEqual(_format_date_for_placeholder(self.VALUE, None), "10/24/2026")
        self.assertEqual(_format_date_for_placeholder(self.VALUE, ""), "10/24/2026")

    def test_unrecognized_placeholder_falls_back(self):
        self.assertEqual(_format_date_for_placeholder(self.VALUE, "pick a date"), "10/24/2026")


class _Cell:
    def __init__(self, text: str, checkbox: bool = False, colspan: str | None = None):
        self.text = text
        self.checkbox = checkbox
        self.colspan = colspan

    def inner_text(self) -> str:
        return self.text

    def get_attribute(self, name: str):
        if name == "colspan":
            return self.colspan
        return None

    def locator(self, selector: str):
        if selector == "input[type=checkbox]":
            return _CountControl(1 if self.checkbox else 0, "checkbox")
        return _CountControl(0, "other")


class _Cells:
    def __init__(self, values):
        self.values = [value if isinstance(value, _Cell) else _Cell(value) for value in values]

    @property
    def first(self):
        return self.values[0] if self.values else None

    def count(self) -> int:
        return len(self.values)

    def nth(self, index: int):
        return self.values[index]


class _Link:
    def click(self) -> None:
        pass


class _Links:
    def __init__(self, n: int):
        self._n = n
        self.first = _Link()

    def count(self) -> int:
        return self._n


class _Row:
    def __init__(self, cells):
        self._cells = cells

    def locator(self, selector: str):
        if selector == "td":
            return self._cells
        if selector == "a":
            return _Links(1)
        return _Cells([])

    def click(self) -> None:
        pass


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def count(self) -> int:
        return len(self._rows)

    def nth(self, index: int):
        return self._rows[index]


class _TablePage:
    def __init__(self, rows):
        self._rows = rows

    def locator(self, selector: str):
        return _Rows(self._rows) if selector == "tbody tr" else _Rows([])

    def get_by_label(self, label: str, exact: bool = True):
        return _DetailControl(self._details.get(label))

    def wait_for_timeout(self, ms: int) -> None:
        pass


class _DetailElement:
    def __init__(self, value):
        self._value = value

    def input_value(self):
        return self._value


class _DetailControl:
    def __init__(self, value):
        self._value = value
        self.first = _DetailElement(value)

    def count(self) -> int:
        return 1 if self._value is not None else 0


class _ReadbackPage(_TablePage):
    def __init__(self, tenant: str, domain: str, details: dict, cells=None):
        # Default to the live tenant table's label/value/empty pattern.
        default = _Cells(["Company name", tenant, "", "Company primary domain", domain, ""])
        super().__init__([_Row(cells if cells is not None else default)])
        self._details = details


class ExactTenantRowTests(unittest.TestCase):
    @staticmethod
    def _page(rows):
        # Mirror the live tenant table: each column is a label cell followed by
        # its value cell, with an empty spacer between pairs.
        return _TablePage([_Row(_Cells(["Company name", company, "",
                                        "Company primary domain", domain, ""]))
                            for company, domain in rows])

    def test_empty_table_is_clear(self):
        self.assertEqual(_exact_tenant_rows(self._page([]), TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_exact_name_match_is_found(self):
        self.assertEqual(_exact_tenant_rows(self._page([(TENANT, "other.example")]), TENANT, MAIN_DOMAIN), "duplicate_found")

    def test_exact_domain_match_is_found(self):
        self.assertEqual(_exact_tenant_rows(self._page([("Other Company", MAIN_DOMAIN)]), TENANT, MAIN_DOMAIN), "duplicate_found")

    def test_prefix_overlap_is_ambiguous(self):
        self.assertEqual(_exact_tenant_rows(self._page([(TENANT + " Extra", "other.example")]), TENANT, MAIN_DOMAIN), "duplicate_ambiguous")

    def test_oversized_table_is_unavailable(self):
        self.assertEqual(_exact_tenant_rows(self._page([("C%d" % i, "d%d.example" % i) for i in range(101)]), TENANT, MAIN_DOMAIN),
                         "duplicate_schema_unavailable")

    def test_short_row_is_unavailable(self):
        page = _TablePage([_Row(_Cells(["only-one-cell"]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_empty_state_row_is_clear(self):
        # The MUIDataTable zero-result row: a single full-width cell (colspan > 1)
        # means the search matched nothing, which is a clean duplicate check.
        page = _TablePage([_Row(_Cells([_Cell("No records found", colspan="5")]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_single_cell_row_without_colspan_is_unavailable(self):
        # A short row that is not a genuine empty-state row stays fail-closed.
        page = _TablePage([_Row(_Cells([_Cell("No records found", colspan="1")]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_empty_state_row_among_data_rows_is_unavailable(self):
        # An empty-state row is only legitimate when it is the only row.
        page = _TablePage([
            _Row(_Cells([_Cell("No records found", colspan="5")])),
            _Row(_Cells(["Other Company", "other.example"])),
        ])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_stacked_empty_state_row_is_clear(self):
        # Live 2026-09-29 shape: the stacked-layout zero-result row renders as a
        # blank label cell plus the message cell (two cells, no colspan).
        page = _TablePage([_Row(_Cells(["", "Sorry, no matching records found"]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_stacked_empty_state_variants_are_clear(self):
        for cells in (["", "No records found."], ["", "  No Matching Records Found  ", ""],
                      ["no records found", ""]):
            with self.subTest(cells=cells):
                page = _TablePage([_Row(_Cells(cells))])
                self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_short_row_with_unknown_text_is_unavailable(self):
        for cells in (["", "Loading..."], ["Company name", "Sorry, no matching records found"],
                      ["", "No records found", "extra"], ["", "No records found for Acme"]):
            with self.subTest(cells=cells):
                page = _TablePage([_Row(_Cells(cells))])
                self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_stacked_empty_state_among_data_rows_is_unavailable(self):
        page = _TablePage([
            _Row(_Cells(["", "Sorry, no matching records found"])),
            _Row(_Cells(["Company name", "Other Company", "", "Company primary domain", "other.example", ""])),
        ])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_four_cell_empty_state_shape_is_unavailable(self):
        page = _TablePage([_Row(_Cells(["", "No records found", "", ""]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_checkbox_column_is_skipped_for_match(self):
        # A leading selectable-row checkbox cell must not be read as the name.
        page = _TablePage([_Row(_Cells([_Cell("", checkbox=True), "Company name", TENANT, "",
                                        "Company primary domain", "other.example", ""]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_found")

    def test_checkbox_column_is_skipped_for_clear(self):
        page = _TablePage([_Row(_Cells([_Cell("", checkbox=True), "Company name", "Other Company", "",
                                        "Company primary domain", "other.example", ""]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_missing_labels_fail_closed(self):
        # A data row that lacks the label cells cannot be resolved and fails closed.
        page = _TablePage([_Row(_Cells([TENANT, "other.example"]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")

    def test_company_name_resolved_by_label_not_position(self):
        # The company value is read from the cell after the "Company name" label,
        # not from a fixed column index, so column reordering cannot false-clear.
        page = _TablePage([_Row(_Cells(["Company primary domain", "other.example", "",
                                        "Company name", TENANT, ""]))])
        self.assertEqual(_exact_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_found")


class _SettlingTablePage(_TablePage):
    """A table whose rows change after each wait (a loading row settling)."""

    def __init__(self, frames):
        super().__init__(frames[0])
        self._frames = frames
        self.waits = 0

    def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1
        self._rows = self._frames[min(self.waits, len(self._frames) - 1)]


class SettledTenantRowsTests(unittest.TestCase):
    LOADING = [_Row(_Cells(["", "Loading..."]))]
    EMPTY = [_Row(_Cells(["", "Sorry, no matching records found"]))]

    def test_loading_row_settles_to_clear(self):
        page = _SettlingTablePage([self.LOADING, self.LOADING, self.EMPTY])
        self.assertEqual(runner._settled_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_clear")

    def test_loading_row_settles_to_found(self):
        found = [_Row(_Cells(["Company name", TENANT, "", "Company primary domain", MAIN_DOMAIN, ""]))]
        page = _SettlingTablePage([self.LOADING, self.LOADING, found])
        self.assertEqual(runner._settled_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_found")

    def test_table_that_never_settles_fails_closed(self):
        page = _SettlingTablePage([self.LOADING])
        self.assertEqual(runner._settled_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_schema_unavailable")
        self.assertEqual(page.waits, 1 + runner.TABLE_SETTLE_RETRIES)

    def test_classifiable_result_is_not_reread(self):
        found = [_Row(_Cells(["Company name", TENANT, "", "Company primary domain", MAIN_DOMAIN, ""]))]
        # Frame 0 is before the search applies; the first read follows one wait.
        page = _SettlingTablePage([self.LOADING, found, self.EMPTY])
        self.assertEqual(runner._settled_tenant_rows(page, TENANT, MAIN_DOMAIN), "duplicate_found")
        self.assertEqual(page.waits, 1)


class ApiSearchResultTests(unittest.TestCase):
    @staticmethod
    def _result(rows, total=None):
        return runner.TenantSearchResult(rows, len(rows) if total is None else total)

    @staticmethod
    def _row(name=TENANT, domain=MAIN_DOMAIN, surface_id=SURFACE_ID, uuid=UUID, last=None):
        return {"accountName": name, "accountDomain": domain, "id": surface_id, "accountUuid": uuid,
                "lastReconScan": last}

    def test_api_duplicate_classification(self):
        cases = [
            ([], None, "duplicate_clear"),
            ([self._row(name="Other Co", domain="other.example")], None, "duplicate_clear"),
            ([self._row(domain="other.example")], None, "duplicate_found"),
            ([self._row(name="Other Co")], None, "duplicate_found"),
            ([self._row(name="Other Co", domain="other.example")], 11, "duplicate_ambiguous"),
        ]
        for rows, total, expected in cases:
            with self.subTest(expected=expected, total=total):
                self.assertEqual(runner._api_duplicate(self._result(rows, total), TENANT, MAIN_DOMAIN), expected)

    def test_api_readback_reads_unscanned_tenant(self):
        self.assertEqual(runner._api_readback(self._result([self._row()]), TENANT, MAIN_DOMAIN),
                         (SURFACE_ID, UUID, "No scan started"))

    def test_api_readback_fails_closed(self):
        cases = {
            "two exact rows": [self._row(), self._row()],
            "no exact row": [self._row(name=TENANT + " test")],
            "domain mismatch": [self._row(domain="other.example")],
            "bad id": [self._row(surface_id="short")],
            "bad uuid": [self._row(uuid="NOT-A-UUID")],
            "scanned tenant": [self._row(last="2026-09-01T00:00:00Z")],
        }
        for label, rows in cases.items():
            with self.subTest(label):
                self.assertIsNone(runner._api_readback(self._result(rows), TENANT, MAIN_DOMAIN))

    def test_api_readback_without_domain_for_override(self):
        rows = [self._row(name="Sample Company - CE Only test", domain="sample-cotest.example")]
        self.assertEqual(runner._api_readback(self._result(rows), "Sample Company - CE Only test", None),
                         (SURFACE_ID, UUID, "No scan started"))


class ReadbackDetailsTests(unittest.TestCase):
    def test_reads_all_three_values(self):
        page = _ReadbackPage(TENANT, MAIN_DOMAIN, {
            "Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning",
        })
        self.assertEqual(_readback_details(page, TENANT), (SURFACE_ID, UUID, "Account Scanning"))

    def test_returns_none_when_tenant_row_absent(self):
        page = _ReadbackPage("Other Company", MAIN_DOMAIN, {})
        self.assertIsNone(_readback_details(page, TENANT))

    def test_returns_none_when_a_detail_value_is_missing(self):
        page = _ReadbackPage(TENANT, MAIN_DOMAIN, {
            "Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": None,
        })
        self.assertIsNone(_readback_details(page, TENANT))

    def test_reads_all_three_values_with_checkbox_column(self):
        page = _ReadbackPage(TENANT, MAIN_DOMAIN, {
            "Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning",
        }, cells=_Cells([_Cell("", checkbox=True), "Company name", TENANT, "",
                         "Company primary domain", MAIN_DOMAIN, ""]))
        self.assertEqual(_readback_details(page, TENANT), (SURFACE_ID, UUID, "Account Scanning"))


# Accessible labels of the Add Customer Account form controls, mirroring the
# CE-only fill contract (runner.build_ce_only_fill). Text controls are filled
# by label; native selects are set by option label text; the two license date
# controls carry no label and are resolved by their data-am attribute in the
# runner (the fake maps those data-am values to "Start date"/"Expiration date").
FILL_LABELS = (
    "Company name",
    "Company primary domain",
    "User email domains  (Comma Separated Values)",
    "First name",
    "Last name",
    "Organization Email",
    "Leaked Credentials scanned domains (Comma Separated Values)",
    "Number of assets",
    "Number of domains",
    "Number of subdomains",
    # Present on the live form (2026-09-24 inventory); only the Surface route
    # fills or verifies them. They start empty like the live controls.
    "Alternate Domains (Comma Separated Values)",
    "SubDomains (Comma Separated Values)",
    "Networks (Comma Separated Values)",
    "Phone number",
    "Job title",
)
# Under the collapsed "Advanced options" section (live probe 2026-09-29: a
# type=number input without name/id/data-am, labelled by its MuiFormControl).
ADVANCED_FILL_LABELS = ("Maximum scan Duration (hours)",)
# Live defaults of text/number inputs (2026-09-29 probe); the runner must
# overwrite and re-read them.
LIVE_TEXT_DEFAULTS = {"Number of subdomains": "50000", "Maximum scan Duration (hours)": "24"}
LC_DEPENDENT_LABELS = ("Leaked Credentials scanning interval",
                       "Leaked Credentials scanned domains (Comma Separated Values)")
SELECT_LABELS = (
    "Account Type",
    "Country",
    "Scanning interval",
    "Leaked Credentials scanning interval",
    "Type",
)
DATE_LABELS = ("Start date", "Expiration date")
DETAIL_LABELS = ("Surface Account ID", "Account UUID", "Account Scanning")
# Live form defaults (2026-09-24 diagnostics): the fake form starts from the
# app defaults so the runner must actively set every toggle.
DEFAULT_CHECKBOX_STATES = {
    "mfaRequired": True,
    "scan_now": True,
    "notificationsAllowed": True,
    "multipleUsersAllowed": True,
    "apiAccessAllowed": True,
    "phishingEnabled": False,
    "leakedCredentialsAllowed": False,
    "provisioningEnabled": True,
    "subDomainsNumberAllowed": True,
    # Under the collapsed "Advanced options" section on the live form
    # (2026-09-29 probe): three default ON, the rest default OFF.
    "automatedDiscoveryEnabled": True,
    "subDomainsReconEnabled": True,
    "webDictionaryBruteForceEnabled": True,
    "webDorkingEnabled": False,
    "fullNucleiScanEnabled": False,
    "authenticatedTestingEnabled": False,
    "staticOutboundIpEnabled": False,
    "aiEnabled": False,
    "multipleAttackStacksEnabled": False,
}
SELECT_OPTIONS = {
    "Account Type": ["Customer", "Demo"],
    "Country": ["France", "Germany", "Israel", "Sweden", "United States"],
    "Scanning interval": ["None", "Daily", "Weekly", "Monthly"],
    "Leaked Credentials scanning interval": ["None", "Daily", "Weekly", "Monthly"],
    "Type": ["Evaluation", "Prepaid annual subscription"],
}


class _CountControl:
    def __init__(self, count: int, source: str):
        self._count = count
        self.source = source

    def count(self) -> int:
        return self._count


class _LocatePage:
    def __init__(self, label_count: int, data_am_count: int, label_lookup_id: str | None = None):
        self._label_count = label_count
        self._data_am_count = data_am_count
        self._label_lookup_id = label_lookup_id

    def get_by_label(self, label: str, exact: bool = True):
        return _CountControl(self._label_count, "label")

    def locator(self, selector: str):
        return _CountControl(self._data_am_count, "data-am")

    def evaluate(self, script: str, *args):
        # Simulates the in-page associated-label lookup: returns the id of the
        # single matching input, or None when zero/ambiguous.
        return self._label_lookup_id

    def get_by_id(self, element_id: str):
        if self._label_lookup_id and element_id == self._label_lookup_id:
            return _CountControl(1, "label-text")
        return _CountControl(0, "label-text")


class LocateFormFieldTests(unittest.TestCase):
    DATA_AM = "Input_Field-addCustomerAccountForm_0_accountName"

    def test_prefers_the_accessible_label(self):
        page = _LocatePage(label_count=1, data_am_count=1)
        control = _locate_form_field(page, "Company name", self.DATA_AM)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, "label")

    def test_falls_back_to_the_data_am_attribute(self):
        page = _LocatePage(label_count=0, data_am_count=1)
        control = _locate_form_field(page, "Company name", self.DATA_AM)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, "data-am")

    def test_falls_back_to_the_associated_label_text(self):
        page = _LocatePage(label_count=0, data_am_count=0, label_lookup_id="uuid-123")
        control = _locate_form_field(page, "Company name", self.DATA_AM)
        self.assertIsNotNone(control)
        self.assertEqual(control.source, "label-text")

    def test_returns_none_when_no_control_resolves(self):
        page = _LocatePage(label_count=0, data_am_count=0)
        self.assertIsNone(_locate_form_field(page, "Company name", self.DATA_AM))

    def test_returns_none_when_ambiguous(self):
        page = _LocatePage(label_count=2, data_am_count=2)
        self.assertIsNone(_locate_form_field(page, "Company name", self.DATA_AM))

    def test_returns_none_when_label_lookup_is_ambiguous(self):
        # evaluate returns None (zero or >1 matches), so the fallback fails closed.
        page = _LocatePage(label_count=0, data_am_count=0, label_lookup_id=None)
        self.assertIsNone(_locate_form_field(page, "Company name", self.DATA_AM))


class CaptureSearchDiagnosticsTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_diag_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def _page(self, inputs):
        page = types.SimpleNamespace(
            url="https://leonardo.dev.app.pentera.io/backoffice/tenantManagement",
            title="Tenant Management",
        )
        page.get_by_role = lambda role: types.SimpleNamespace(count=lambda: 0)
        page.evaluate = lambda script, *args: inputs
        return page

    def test_captures_data_am_and_label_text(self):
        page = self._page([
            {"tag": "input", "type": "text", "id": "uuid-123",
             "data-am": "Input_Field-addCustomerAccountForm_0_accountName", "label": "Company name"},
            {"tag": "input", "type": "text", "id": "uuid-456", "placeholder": "Search"},
        ])
        with patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")):
            runner._capture_search_diagnostics(page)
            raw = json.loads(runner.DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["inputs"]), 2)
        self.assertEqual(raw["inputs"][0]["data-am"], "Input_Field-addCustomerAccountForm_0_accountName")
        self.assertEqual(raw["inputs"][0]["label"], "Company name")
        self.assertEqual(raw["inputs"][1]["placeholder"], "Search")
        # No form values, tenant names, or table contents should be captured.
        self.assertNotIn("Sample Company", json.dumps(raw))

    def test_swallows_evaluate_failures(self):
        page = self._page([])
        def boom(*args):
            raise RuntimeError("evaluate failed")
        page.evaluate = boom
        with patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")):
            runner._capture_search_diagnostics(page)  # must not raise
            raw = json.loads(runner.DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["inputs"], [])


class _RPControl:
    def __init__(self, page, label: str, kind: str):
        self.page = page
        self.label = label
        self.kind = kind
        self.first = self

    def count(self) -> int:
        if self.kind in ("fill", "select", "date"):
            # The Add Account form controls exist only while the form is open
            # (it closes on the single Confirm click or on Cancel).
            return 0 if (self.page.confirmed or self.page.cancelled) else 1
        if self.kind == "advanced_fill":
            # Advanced options text controls exist only once the section is
            # expanded (and only when the scenario's form has them).
            return 1 if (self.page.max_duration_control and self.page.advanced_open
                         and not (self.page.confirmed or self.page.cancelled)) else 0
        if self.kind == "detail":
            # Details-page controls are reachable only once the exact tenant
            # row exists (after a matching search in both flows).
            return 1 if (self.page.tenants and self.page.details.get(self.label) is not None) else 0
        if self.kind in ("search", "button", "confirm"):
            return 1
        if self.kind == "cancel":
            return 0 if (self.page.confirmed or self.page.cancelled) else 1
        if self.kind == "advanced":
            return 2
        return 0

    def is_enabled(self) -> bool:
        if self.label in LC_DEPENDENT_LABELS:
            # Live form: the Leaked Credentials interval and domains are
            # disabled while the Leaked Credentials toggle is OFF.
            return bool(self.page.checkbox_states.get("leakedCredentialsAllowed"))
        return True

    def fill(self, value: str, **kwargs) -> None:
        # Filling never confirms: the runner submits by clicking the single
        # Confirm control (auto-confirm), so the form stays open until that click.
        self.page.filled[self.label] = value

    def press(self, key: str) -> None:
        pass

    def press_sequentially(self, value: str, **kwargs) -> None:
        self.fill(value)

    def click(self, **kwargs) -> None:
        if self.kind == "confirm":
            self.page.confirm()
        elif self.kind == "cancel":
            self.page.cancelled = True
        elif self.kind == "advanced":
            # A toggle-style section: each click flips it, like the live button.
            self.page.advanced_clicks += 1
            self.page.advanced_open = not self.page.advanced_open

    def wait_for(self, **kwargs) -> None:
        pass

    def input_value(self):
        if self.kind in ("fill", "date", "advanced_fill"):
            # The runner re-reads every value it fills; the fake must return
            # the stored value, not the (absent) detail value. An untouched
            # live input reads as "".
            return self.page.filled.get(self.label, "")
        return self.page.details.get(self.label)

    def select_option(self, **kwargs) -> None:
        option = kwargs.get("label")
        options = SELECT_OPTIONS.get(self.label, [])
        if option not in options:
            raise ValueError(f"Option {option!r} not found")
        self.page.selected[self.label] = option

    def evaluate(self, script: str, *args):
        # Simulates reading the selected option text of a native <select>.
        return self.page.selected.get(self.label)

    def get_attribute(self, name: str):
        if name == "placeholder" and self.kind == "date":
            return self.page.date_placeholders.get(self.label)
        return None


class _RPCheckboxControl:
    """Fake for one Add Account checkbox, located by its name attribute."""

    def __init__(self, page, key: str):
        self.page = page
        self.key = key
        self.first = self

    def count(self) -> int:
        if self.key not in self.page.checkbox_states:
            return 0
        # Advanced toggles exist only once "Advanced options" is expanded.
        if self.key in runner.ADVANCED_TOGGLES_OFF and not self.page.advanced_open:
            return 0
        # Live form (2026-09-29): "Scan now" exists only while Scanning
        # interval is None; a Weekly/Monthly schedule removes it.
        if self.key == "scan_now" and self.page.selected.get("Scanning interval") not in (None, "None"):
            return 0
        return 1

    def is_checked(self) -> bool:
        return self.page.checkbox_states[self.key]

    def is_enabled(self) -> bool:
        return True

    def click(self, **kwargs) -> None:
        self.page.checkbox_states[self.key] = not self.page.checkbox_states[self.key]


class _RPOperatorControl:
    """Fake for the Operator Account react-select input (placeholder-located)."""

    def __init__(self, page):
        self.page = page
        self.first = self

    def count(self) -> int:
        return 0 if (self.page.confirmed or self.page.cancelled) else self.page.operator_inputs

    def input_value(self) -> str:
        return self.page.filled.get("Operator Account", "")

    def evaluate(self, script: str, *args):
        # Number of selected-value chips in the react-select container.
        return self.page.operator_selected


class _RPRow:
    def __init__(self, company: str, domain: str):
        self.company = company
        self.domain = domain

    def locator(self, selector: str):
        if selector == "td":
            # Mirror the live tenant table: label cell, value cell, empty spacer.
            return _Cells(["Company name", self.company, "",
                           "Company primary domain", self.domain, ""])
        if selector == "a":
            return _Links(1)
        return _Cells([])


class _RPRows:
    def __init__(self, page):
        self.page = page

    def _rows(self):
        if not self.page.tenants and self.page.empty_state_row:
            # The live stacked-layout zero-result row: label cell + message cell.
            return [_Row(_Cells(["", "Sorry, no matching records found"]))]
        return self.page.tenants

    def count(self) -> int:
        return len(self._rows())

    def nth(self, index: int):
        return self._rows()[index]


class _RPPage:
    def __init__(self, scenario: dict):
        self.url = scenario["url"]
        self.tenants = list(scenario.get("tenants", []))
        self.empty_state_row = scenario.get("empty_state_row", False)
        self.handlers: dict[str, list] = {}
        self.cancelled = False
        self.advanced_open = scenario.get("advanced_open", False)
        self.advanced_clicks = 0
        self.create_status = scenario.get("create_status", 200)
        self.search_status = scenario.get("search_status", 200)
        self.details = dict(scenario.get("details", {}))
        self.filled: dict[str, str] = {}
        self.selected: dict[str, str] = {}
        self.checkbox_states: dict[str, bool] = dict(DEFAULT_CHECKBOX_STATES)
        self.date_placeholders: dict[str, str] = {label: "mm/dd/yyyy" for label in DATE_LABELS}
        self.confirmed = False
        self.create_on_confirm = scenario.get("create_on_confirm", True)
        self.tenant = scenario["tenant"]
        self.domain = scenario["domain"]
        self.max_duration_control = scenario.get("max_duration_control", True)
        # Operator Account react-select: one input, empty, no selected chips.
        self.operator_inputs = scenario.get("operator_inputs", 1)
        self.operator_selected = scenario.get("operator_selected", 0)
        # The live max-duration input may only resolve through its enclosing
        # MuiFormControl label (no accessible-label association).
        self.max_duration_by_form_control = scenario.get("max_duration_by_form_control", False)
        self.filled.update(LIVE_TEXT_DEFAULTS)
        self.filled.update(scenario.get("prefilled", {}))

    def confirm(self) -> None:
        if self.confirmed:
            return
        self.confirmed = True
        if self.create_on_confirm:
            self.tenants.append(_RPRow(self.tenant, self.domain))

    def goto(self, url: str, **kwargs) -> None:
        pass

    def wait_for_timeout(self, ms: int) -> None:
        pass

    def on(self, event: str, handler) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def search_payload(self, text: str) -> dict:
        # The live getAllDetailedAccounts shape: rows under
        # pagination_response.table_data, filtered server-side by the text.
        needle = text.casefold()
        scanning = self.details.get("Account Scanning")
        rows = [{"accountName": row.company, "accountDomain": row.domain,
                 "id": self.details.get("Surface Account ID"), "accountUuid": self.details.get("Account UUID"),
                 "lastReconScan": "2026-09-01T00:00:00Z" if scanning else None}
                for row in self.tenants
                if needle and (needle in row.company.casefold() or needle in row.domain.casefold())]
        return {"pagination_response": {"table_data": rows, "total_count": len(rows)}}

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=None):
        # Mirrors the live app: a Confirm click posts account/add; a search edit
        # posts getAllDetailedAccounts carrying the search text. The runner's
        # predicate is applied, so a wrong match raises like a real timeout.
        info = types.SimpleNamespace()
        confirmed_before = self.confirmed
        yield info
        if self.confirmed and not confirmed_before:
            response = types.SimpleNamespace(
                url=runner.DEVELOPMENT_ORIGIN + "/api/v1/backoffice/account/add", status=self.create_status,
                request=types.SimpleNamespace(method="POST", post_data="{}"))
        else:
            text = self.filled.get("Search", "")
            body = json.dumps({"filters": {"and": [{"method": "contains", "value": text}]}})
            response = types.SimpleNamespace(
                url=runner.DEVELOPMENT_ORIGIN + "/api/v1/backoffice/getAllDetailedAccounts", status=self.search_status,
                request=types.SimpleNamespace(method="POST", post_data=body),
                json=lambda: self.search_payload(text))
        if not predicate(response):
            raise TimeoutError("fake: no matching response")
        info.value = response

    def locator(self, selector: str):
        if selector == runner.ADD_ACCOUNT_MODAL_SELECTOR:
            page = self

            class _Modal:
                def get_by_role(self, role, name, exact=True):
                    return _RPControl(page, "Cancel", "cancel") if (role, name) == ("button", "Cancel") \
                        else _RPControl(page, name, "other")

                def get_by_text(self, text, exact=False):
                    # The live form matches "Advanced options" twice (inner div
                    # and button text); the runner uses .first.
                    return _RPControl(page, text, "advanced") if text == runner.ADVANCED_OPTIONS_TEXT \
                        else _RPControl(page, text, "other")
            return _Modal()
        if selector.startswith('select[name="'):
            # The live selects have no associated <label> (2026-09-29): they
            # resolve only by their name attribute, never by get_by_label.
            name = selector[len('select[name="'):-2]
            label = {v: k for k, v in runner.SELECT_NAMES.items()}.get(name)
            return _RPControl(self, label, "select") if label else _RPControl(self, name, "other")
        if selector.startswith('input[type=checkbox][name="'):
            # The selector is input[type=checkbox][name="<key>"]; strip the
            # prefix and the trailing '"]'.
            key = selector[len('input[type=checkbox][name="'):-2]
            return _RPCheckboxControl(self, key)
        if selector == runner.OPERATOR_ACCOUNT_INPUT_SELECTOR:
            return _RPOperatorControl(self)
        prefix = '.MuiFormControl-root:has(> label:text-is("'
        if selector.startswith(prefix):
            label = selector[len(prefix):selector.index('")')]
            if label in ADVANCED_FILL_LABELS and self.max_duration_by_form_control:
                return _RPControl(self, label, "advanced_fill")
            return _RPControl(self, label, "other")
        if selector.startswith('[data-am="'):
            # The live form's license date inputs carry no label, name, id, or
            # placeholder; they are located by their data-am attribute. Map the
            # two known date data-am values to the fake date controls; any other
            # data-am resolves to nothing.
            data_am = selector[len('[data-am="'):-2]
            label = {"AddEditTenantModal-date-startDate": "Start date",
                     "AddEditTenantModal-date-expirationDate": "Expiration date"}.get(data_am)
            if label is None:
                return _RPControl(self, "missing", "other")
            return _RPControl(self, label, "date")
        return _RPRows(self)

    def get_by_label(self, label: str, exact: bool = True):
        if label in FILL_LABELS:
            kind = "fill"
        elif label in DATE_LABELS:
            kind = "date"
        elif label in ADVANCED_FILL_LABELS:
            kind = "other" if self.max_duration_by_form_control else "advanced_fill"
        elif label in DETAIL_LABELS:
            kind = "detail"
        else:
            kind = "other"
        return _RPControl(self, label, kind)

    def get_by_role(self, role: str, name: str, exact: bool = True):
        if role == "textbox" and name == "Search":
            return _RPControl(self, "Search", "search")
        if role == "button" and name == "Add Account":
            return _RPControl(self, "Add Account", "button")
        if role == "button" and name == "Confirm":
            return _RPControl(self, "Confirm", "confirm")
        if role == "checkbox" and name == "primary checkbox":
            # The "Scan now" control has no name/id; it is located by role.
            return _RPCheckboxControl(self, "scan_now")
        return _RPControl(self, name, "other")


class _FakeBrowser:
    def close(self) -> None:
        pass


class _FakeProc:
    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


def _fake_tab() -> runner._AutomationTab:
    # A temporary (non-persisted) tab: release terminates the fake process and
    # removes a non-existent temp profile path, exactly like the old teardown.
    return runner._AutomationTab(port=0, target_id="T-RUN", preexisting=frozenset(),
                                 profile_dir=Path(tempfile.gettempdir()) / "ce-test-profile-absent",
                                 persist=False, chrome_proc=_FakeProc(), reused=False)


class _FakeContext:
    def __init__(self, page):
        self.pages = [page]


class _RPPlaywrightContext:
    def __init__(self, page, tracker: dict):
        self.page = page
        self.tracker = tracker

    def __enter__(self):
        return object()

    def __exit__(self, *exc) -> bool:
        return False


class RunEndToEndTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_run_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def _install_fake_playwright(self, scenario: dict, tracker: dict) -> "_RPPage":
        page = _RPPage(scenario)

        def sync_playwright():
            return _RPPlaywrightContext(page, tracker)

        package = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = sync_playwright
        package.sync_api = sync_api
        previous = {key: sys.modules.get(key) for key in ("playwright", "playwright.sync_api")}

        def restore() -> None:
            for key, value in previous.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

        sys.modules["playwright"] = package
        sys.modules["playwright.sync_api"] = sync_api
        self.addCleanup(restore)
        return page

    def _attach(self, page: "_RPPage", tracker: dict):
        def attach(playwright):
            tracker["launched"] = True
            tracker["context"] = True
            return (_FakeBrowser(), _FakeContext(page), page, _fake_tab())
        return attach

    def _attach_login_timeout(self, tracker: dict):
        def attach(playwright):
            tracker["launched"] = True
            raise runner.LoginTimeout()
        return attach

    def _source(self, revision: str = REVISION) -> runner.CeFillSource:
        names = ce_only_names(ACCOUNT)
        return runner.CeFillSource(
            reference="CO-0702", source_revision=revision, account_id="a123456789012345",
            account_name=ACCOUNT, email_domain=EMAIL_DOMAIN, country="France",
            subscription_start=date(2026, 9, 28), subscription_end=date(2029, 9, 27),
            tenant_name=names.tenant_name, primary_user_alias=names.primary_user_alias,
        )

    def test_readback_verified_happy_path(self):
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "readback_verified")
        self.assertTrue(tracker.get("launched"))
        self.assertTrue(tracker.get("context"))
        # The full fill contract must have been applied to the fake form.
        plan = build_ce_only_fill(self._source())
        for label, expected in plan["texts"].items():
            self.assertEqual(page.filled.get(label), expected)
        self.assertEqual(page.selected, plan["selects"])
        self.assertEqual(page.checkbox_states, plan["checkboxes"])
        # The fake date controls are typed text inputs (the live form uses the
        # picker path). Start = the run day; expiration = the contract rule.
        self.assertEqual(page.filled["Start date"], runner._run_day().isoformat())
        self.assertEqual(page.filled["Expiration date"], "2027-09-27")

    def _duplicate_check(self, page, tracker):
        state = self._temp_path("state.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", state), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run_duplicate_check("CO-0702")
        # Read-only: never opens Add Account, never confirms, never touches the gate.
        self.assertFalse(page.confirmed)
        self.assertFalse(state.exists())
        self.assertNotIn("Company name", page.filled)
        return result

    def test_duplicate_check_clear(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        self.assertEqual(self._duplicate_check(page, tracker), "duplicate_check_clear")
        run_log = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]
        self.assertEqual((run_log["mode"], run_log["result"]), ("duplicate_check", "duplicate_check_clear"))
        lookups = [e.get("field") for e in run_log["events"] if e["step"] == "duplicate_check"]
        self.assertEqual(lookups, ["tenant_name", "primary_domain"])

    def test_duplicate_check_found_by_name(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(tenants=[_RPRow(TENANT, "other.example")]), tracker)
        self.assertEqual(self._duplicate_check(page, tracker), "duplicate_check_found")

    def test_duplicate_check_found_by_domain_only_in_server_rows(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(_RPPage, "search_payload", lambda self, text: {"pagination_response": {
                "table_data": [{"accountName": "Other Co", "accountDomain": MAIN_DOMAIN}], "total_count": 1}}):
            self.assertEqual(self._duplicate_check(page, tracker), "duplicate_check_found")

    def test_duplicate_check_ambiguous_when_results_exceed_rows(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(_RPPage, "search_payload", lambda self, text: {"pagination_response": {
                "table_data": [], "total_count": 3}}):
            self.assertEqual(self._duplicate_check(page, tracker), "duplicate_check_ambiguous")

    def test_expired_session_is_reported_by_name_in_a_create_run(self):
        # Live 2026-09-29 CO-0728: the tab showed Tenant Management but the
        # search API answered 401.
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(search_status=401), tracker)
        result = self._run_scenario(page, tracker)
        self.assertEqual(result, "leonardo_session_expired")
        self.assertFalse(page.confirmed)
        self.assertNotIn("Company name", page.filled)

    def test_expired_session_is_reported_by_name_in_a_duplicate_check(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(search_status=403), tracker)
        self.assertEqual(self._duplicate_check(page, tracker), "leonardo_session_expired")

    def test_other_search_errors_stay_schema_unavailable(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(search_status=500), tracker)
        self.assertEqual(self._duplicate_check(page, tracker), "duplicate_schema_unavailable")

    def test_duplicate_check_fails_closed_without_a_search_response(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(_RPPage, "search_payload", lambda self, text: {"unexpected": True}):
            self.assertEqual(self._duplicate_check(page, tracker), "duplicate_schema_unavailable")

    def test_advanced_options_are_expanded_and_the_three_toggles_end_off(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        # The advanced toggles do not exist until the section is expanded.
        self.assertEqual(_RPCheckboxControl(page, "automatedDiscoveryEnabled").count(), 0)
        result = self._run_scenario(page, tracker)
        self.assertEqual(result, "readback_verified")
        self.assertEqual(page.advanced_clicks, 1)
        for key in ("automatedDiscoveryEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled"):
            self.assertIs(page.checkbox_states[key], False, key)
        self.assertEqual(page.checkbox_states, build_ce_only_fill(self._source())["checkboxes"])

    def test_already_open_advanced_options_are_not_collapsed(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(advanced_open=True), tracker)
        result = self._run_scenario(page, tracker)
        self.assertEqual(result, "readback_verified")
        self.assertEqual(page.advanced_clicks, 0)

    def test_missing_advanced_options_button_fails_closed_before_confirm(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original = _RPControl.count
        with patch.object(_RPControl, "count",
                          lambda control: 0 if control.kind == "advanced" else original(control)):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertFalse(page.confirmed)
        events = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]["events"]
        self.assertIn(("advanced_options", "button_not_found"), [(e["step"], e["outcome"]) for e in events])

    def test_advanced_toggle_that_stays_on_fails_closed(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original_click = _RPCheckboxControl.click

        def click(control, **kwargs):
            if control.key == "webDictionaryBruteForceEnabled":
                return  # the switch ignores the click and stays ON
            original_click(control, **kwargs)

        with patch.object(_RPCheckboxControl, "click", click):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertFalse(page.confirmed)

    def test_dry_run_fills_verifies_and_cancels_without_touching_the_gate(self):
        tracker: dict = {}
        state = self._temp_path("state.json")
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", state), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1, dry_run=True)
        self.assertEqual(result, "dry_run_fill_verified")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        self.assertFalse(state.exists())
        # The full contract was filled before the cancel.
        self.assertEqual(page.filled.get("Company name"), TENANT)
        run_log = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]
        self.assertEqual((run_log["mode"], run_log["result"]), ("dry_run", "dry_run_fill_verified"))
        # The dry run checked (never clicked) Confirm before cancelling.
        self.assertIn(("confirm_enable", "enabled"), [(e["step"], e["outcome"]) for e in run_log["events"]])

    def test_redact_domains_masks_domains_and_emails_only(self):
        text = runner._redact_domains("Invalid domain a-b.example.co.uk; mail x.y+z@corp.example; max=72; 1.5 h")
        self.assertEqual(text, "Invalid domain <domain>; mail <domain>; max=72; 1.5 h")

    def test_cli_diagnose_confirm_runs_a_diagnose_dry_run(self):
        with patch.object(runner, "run", return_value="diagnose_confirm_blocker_found") as run, \
                patch.object(sys, "argv", ["runner", "--co", "CO-0649", "--revision", REVISION,
                                           "--route", runner.SURFACE_ENGINE, "--diagnose-confirm"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(), 0)
        run.assert_called_once_with("CO-0649", REVISION, dry_run=False, route=runner.SURFACE_ENGINE, diagnose=True)

    def test_dry_run_fill_failure_still_cancels_and_skips_the_gate(self):
        tracker: dict = {}
        state = self._temp_path("state.json")
        page = self._install_fake_playwright(self._base(), tracker)
        original = page.locator
        page.locator = lambda selector: (_RPControl(page, "Country", "other")
                                         if selector == 'select[name="accountCountry"]' else original(selector))
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", state), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1, dry_run=True)
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertTrue(page.cancelled)
        self.assertFalse(page.confirmed)
        self.assertFalse(state.exists())

    def test_expired_license_stops_before_browser_launch(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(runner, "_run_day", return_value=date(2027, 9, 27)):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "ce_license_dates_unavailable")
        self.assertFalse(tracker.get("launched", False))

    def test_stacked_empty_state_then_create_without_scan_state_is_verified(self):
        # The live 2026-09-29 CO-0679 shape: the no-duplicate search shows the
        # stacked-layout empty-state row (two cells), and the freshly created
        # CE-only tenant has no scan state yet (scanning and Scan now are off).
        tracker: dict = {}
        readbacks = self._temp_path("readbacks.json")
        scenario = {
            "url": runner.TENANT_MANAGEMENT, "tenants": [], "empty_state_row": True, "create_on_confirm": True,
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": None},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "READBACK_PATH", readbacks), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "readback_verified")
        self.assertTrue(page.confirmed)
        raw = json.loads(readbacks.read_text(encoding="utf-8"))
        self.assertEqual(raw["CO-0702"]["leonardo_state"], "No scan started")
        self.assertEqual(raw["CO-0702"]["account_uuid"], UUID)

    def _run_scenario(self, page, tracker):
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            return runner.run("CO-0702", REVISION, review_wait_seconds=1)

    def _base(self, **extra):
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "empty_state_row": True, "create_on_confirm": True,
                    "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": None},
                    "tenant": TENANT, "domain": MAIN_DOMAIN}
        scenario.update(extra)
        return scenario

    def test_create_readback_uses_api_row_when_details_labels_are_absent(self):
        # Live 2026-09-29: the details view does not expose the labelled
        # controls; the server search row still provides the evidence.
        tracker: dict = {}
        readbacks = self._temp_path("readbacks.json")
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(runner, "_readback_details_optional_state", return_value=None), \
                patch.object(runner, "READBACK_PATH", readbacks):
            with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                    patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                    patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                    patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
                result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "readback_verified")
        raw = json.loads(readbacks.read_text(encoding="utf-8"))
        self.assertEqual((raw["CO-0702"]["surface_account_id"], raw["CO-0702"]["account_uuid"],
                          raw["CO-0702"]["leonardo_state"]), (SURFACE_ID, UUID, "No scan started"))

    def test_api_duplicate_blocks_even_when_table_shows_empty(self):
        # The UI table shows the empty-state row but the server rows carry an
        # exact domain match: the independent API check must block.
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        with patch.object(_RPPage, "search_payload", lambda self, text: {"pagination_response": {
                "table_data": [{"accountName": "Other Co", "accountDomain": MAIN_DOMAIN}], "total_count": 1}}):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "duplicate_found")
        self.assertFalse(page.confirmed)

    def test_stale_search_response_fails_closed_before_add_account(self):
        # The server-side search response must carry this lookup; a response
        # for another query (the previous lookup) never counts as clear.
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original_fill = _RPControl.fill

        def sticky_search(control, value, **kwargs):
            if control.label == "Search" and value:
                return original_fill(control, "previous lookup", **kwargs)
            return original_fill(control, value, **kwargs)

        with patch.object(_RPControl, "fill", sticky_search):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "duplicate_schema_unavailable")
        self.assertFalse(page.confirmed)

    def test_disabled_confirm_fails_closed_without_click(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original = _RPControl.is_enabled
        with patch.object(_RPControl, "is_enabled",
                          lambda control: control.kind != "confirm" and original(control)), \
                patch.object(_RPPage, "evaluate", lambda self, script, *a: ["accountCountry"], create=True):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "confirm_button_not_enabled")
        self.assertFalse(page.confirmed)

    def test_date_reverting_on_blur_fails_closed(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original_input = _RPControl.input_value
        with patch.object(_RPControl, "input_value",
                          lambda control: "" if control.kind == "date" else original_input(control)):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertFalse(page.confirmed)

    def test_intercepted_toggle_click_retries_forced(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(), tracker)
        original_click = _RPCheckboxControl.click

        def click(control, **kwargs):
            if not kwargs.get("force"):
                raise TimeoutError("element intercepts pointer events")
            original_click(control, **kwargs)

        with patch.object(_RPCheckboxControl, "click", click):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "readback_verified")

    def test_account_add_error_with_open_form_is_no_create(self):
        tracker: dict = {}
        page = self._install_fake_playwright(self._base(create_on_confirm=False, create_status=400), tracker)
        original_count = _RPControl.count
        # The form stays open after a rejected create.
        with patch.object(_RPControl, "count",
                          lambda control: 1 if control.kind == "fill" else original_count(control)):
            result = self._run_scenario(page, tracker)
        self.assertEqual(result, "confirm_no_create")

    def test_create_with_unrecognized_scan_state_is_mismatch(self):
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Paused"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "readback_value_mismatch")

    def test_source_revision_drift_stops_before_browser_launch(self):
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source("2026-09-18T00:00:00Z")), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "source_revision_drift")
        self.assertFalse(tracker.get("launched", False))

    def test_salesforce_id_already_present_stops_before_browser_launch(self):
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        source = dataclasses.replace(self._source(), salesforce_id_present=True)
        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run), patch.object(runner, "ce_fill_source", return_value=source), \
                    patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                    patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
                result = runner.run("CO-0702", REVISION, review_wait_seconds=1, dry_run=dry_run)
            self.assertEqual(result, "salesforce_id_already_present")
            self.assertFalse(tracker.get("launched", False))

    def test_development_login_timeout(self):
        tracker: dict = {}
        scenario = {"url": runner.DEVELOPMENT_LOGIN, "tenants": [], "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach_login_timeout(tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "development_login_timeout")
        self.assertTrue(tracker.get("launched"))

    def test_confirm_no_create_when_form_closes_without_tenant(self):
        # Auto-confirm clicks Confirm, the form closes, but no tenant is created
        # (create_on_confirm=False). The bounded re-search stays clear, so the
        # runner fails closed with confirm_no_create (no retry).
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": False,
                    "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "confirm_no_create")

    def test_confirm_button_schema_unavailable(self):
        # No single Confirm control is present, so the runner fails closed
        # before submitting anything.
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
                    "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        original_get_by_role = page.get_by_role

        def get_by_role(role, name, exact=True):
            if name == "Confirm":
                return types.SimpleNamespace(count=lambda: 0)
            return original_get_by_role(role, name, exact=exact)

        page.get_by_role = get_by_role
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "confirm_button_schema_unavailable")

    def test_readback_value_mismatch(self):
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
                    "details": {"Surface Account ID": "short", "Account UUID": UUID, "Account Scanning": "Account Scanning"},
                    "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "readback_value_mismatch")

    def test_fill_value_mismatch_stops_before_confirm(self):
        # A text control that does not keep the filled value fails closed
        # before the Confirm control is ever clicked.
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
                    "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        original_fill = _RPControl.fill

        def broken_fill(self, value: str, **kwargs) -> None:
            if self.label == "Company name":
                return  # the control drops the value
            original_fill(self, value, **kwargs)

        with patch.object(_RPControl, "fill", broken_fill), \
                patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "fill_value_mismatch")
        self.assertFalse(page.confirmed)

    def test_fill_form_schema_unavailable_when_a_control_is_missing(self):
        # A missing form control (e.g. a renamed label) fails closed before
        # the form is submitted.
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "create_on_confirm": True,
                    "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        original_get_by_label = page.get_by_label

        def get_by_label(label, exact=True):
            if label == "Organization Email":
                return types.SimpleNamespace(count=lambda: 0)
            return original_get_by_label(label, exact=exact)

        page.get_by_label = get_by_label
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertFalse(page.confirmed)


class ReadbackOnlyTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_readback_only_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def _install_fake_playwright(self, scenario: dict, tracker: dict) -> "_RPPage":
        page = _RPPage(scenario)

        def sync_playwright():
            return _RPPlaywrightContext(page, tracker)

        package = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = sync_playwright
        package.sync_api = sync_api
        previous = {key: sys.modules.get(key) for key in ("playwright", "playwright.sync_api")}

        def restore() -> None:
            for key, value in previous.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

        sys.modules["playwright"] = package
        sys.modules["playwright.sync_api"] = sync_api
        self.addCleanup(restore)
        return page

    def _attach(self, page: "_RPPage", tracker: dict):
        def attach(playwright):
            tracker["launched"] = True
            return (_FakeBrowser(), _FakeContext(page), page, _fake_tab())
        return attach

    def _source(self) -> runner.CeFillSource:
        names = ce_only_names(ACCOUNT)
        return runner.CeFillSource(
            reference="CO-0702", source_revision=REVISION, account_id="a123456789012345",
            account_name=ACCOUNT, email_domain=EMAIL_DOMAIN, country="France",
            subscription_start=date(2026, 9, 28), subscription_end=date(2029, 9, 27),
            tenant_name=names.tenant_name, primary_user_alias=names.primary_user_alias,
        )

    def test_readback_only_verified_with_scanning_state(self):
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(TENANT, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "readback_only_verified")
        raw = json.loads(readback_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["CO-0702"]["leonardo_state"], "Account Scanning")
        self.assertEqual(raw["CO-0702"]["surface_account_id"], SURFACE_ID)
        self.assertEqual(raw["CO-0702"]["account_uuid"], UUID)

    def test_readback_only_records_no_scan_started(self):
        # A tenant with no scan yet has no observed state; the readback
        # records the fixed label "No scan started".
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(TENANT, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "readback_only_verified")
        raw = json.loads(readback_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["CO-0702"]["leonardo_state"], "No scan started")

    def test_readback_only_tenant_not_found(self):
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {},
                    "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "readback_only_tenant_not_found")
        self.assertFalse(readback_path.exists())

    def test_readback_only_state_unrecognized(self):
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(TENANT, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Paused"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "readback_only_state_unrecognized")
        self.assertFalse(readback_path.exists())

    def test_readback_only_does_not_touch_the_create_gate(self):
        # Readback-only is ungated: it must not write the runner state file.
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(TENANT, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        state_path = self._temp_path("state.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", state_path), \
                patch.object(runner, "READBACK_PATH", self._temp_path("readbacks.json")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "readback_only_verified")
        self.assertFalse(state_path.exists())

    def test_readback_only_source_unavailable(self):
        tracker: dict = {}
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {},
                    "tenant": TENANT, "domain": MAIN_DOMAIN}
        page = self._install_fake_playwright(scenario, tracker)
        with patch.object(runner, "ce_fill_source", side_effect=RuntimeError("salesforce_fill_source_unavailable")), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "salesforce_fill_source_unavailable")
        self.assertFalse(tracker.get("launched", False))

    def test_readback_only_tenant_name_override_verifies(self):
        # A dev tenant whose name intentionally deviates from the contract name
        # (a " test" suffix) is verified by passing the live name as an override.
        tracker: dict = {}
        live_name = TENANT + " test"
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(live_name, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID},
            "tenant": live_name, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702", tenant_name_override=live_name)
        self.assertEqual(result, "readback_only_verified")
        raw = json.loads(readback_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["CO-0702"]["leonardo_state"], "No scan started")

    def test_readback_only_without_override_is_ambiguous_for_suffixed_tenant(self):
        # Without the override, the contract name is a prefix of the live
        # " test"-suffixed name, so the readback fails closed as ambiguous.
        # A non-matching domain isolates the company-name prefix behavior.
        tracker: dict = {}
        live_name = TENANT + " test"
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(live_name, "other.example")],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID},
            "tenant": live_name, "domain": "other.example",
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702")
        self.assertEqual(result, "duplicate_ambiguous")
        self.assertFalse(readback_path.exists())

    def test_readback_only_blank_override_falls_back_to_contract_name(self):
        # A blank override is ignored; the contract name is used instead.
        tracker: dict = {}
        scenario = {
            "url": runner.TENANT_MANAGEMENT,
            "tenants": [_RPRow(TENANT, MAIN_DOMAIN)],
            "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID, "Account Scanning": "Account Scanning"},
            "tenant": TENANT, "domain": MAIN_DOMAIN,
        }
        page = self._install_fake_playwright(scenario, tracker)
        readback_path = self._temp_path("readbacks.json")
        with patch.object(runner, "ce_fill_source", return_value=self._source()), \
                patch.object(runner, "READBACK_PATH", readback_path), \
                patch.object(runner, "_attach_attended_browser", self._attach(page, tracker)):
            result = run_readback("CO-0702", tenant_name_override="   ")
        self.assertEqual(result, "readback_only_verified")


class ReadbackStatesTests(unittest.TestCase):
    def test_accepted_states(self):
        self.assertEqual(READBACK_STATES, frozenset({"Account Scanning", "No scan started"}))

    def test_write_rejects_unrecognized_state(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_state_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        with patch.object(runner, "READBACK_PATH", directory / "readbacks.json"):
            with self.assertRaisesRegex(ValueError, "invalid_readback_state"):
                write_readback_evidence("CO-0702", SURFACE_ID, UUID, "2026-09-25", state="Account Paused")

    def test_write_accepts_no_scan_started(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_state_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        with patch.object(runner, "READBACK_PATH", directory / "readbacks.json"):
            write_readback_evidence("CO-0702", SURFACE_ID, UUID, "2026-09-25", state="No scan started")
            raw = json.loads(runner.READBACK_PATH.read_text(encoding="utf-8"))
            self.assertEqual(raw["CO-0702"]["leonardo_state"], "No scan started")


class BrowserLaunchHelperTests(unittest.TestCase):
    def test_free_port_returns_a_valid_port(self):
        port = runner._free_port()
        self.assertIsInstance(port, int)
        self.assertTrue(0 < port < 65536)

    def test_chrome_executable_is_none_or_existing_path(self):
        result = runner._chrome_executable()
        if result is not None:
            self.assertIsInstance(result, str)
            self.assertTrue(Path(result).exists())

    def test_wait_for_cdp_returns_false_for_dead_port(self):
        port = runner._free_port()
        self.assertFalse(runner._wait_for_cdp(port, timeout_seconds=1.0))

    def test_wait_for_cdp_returns_true_for_live_endpoint(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertTrue(runner._wait_for_cdp(port, timeout_seconds=5.0))

    def test_cdp_page_urls_returns_empty_for_dead_port(self):
        port = runner._free_port()
        self.assertEqual(runner._cdp_page_urls(port), [])

    def test_cdp_page_urls_parses_only_page_targets(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = json.dumps([
            {"type": "page", "url": runner.TENANT_MANAGEMENT},
            {"type": "page", "url": "https://example.com/x"},
            {"type": "service_worker", "url": "https://example.com/sw"},
            {"type": "other"},
        ]).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertEqual(runner._cdp_page_urls(port), [runner.TENANT_MANAGEMENT, "https://example.com/x"])

    def test_cdp_page_urls_returns_empty_for_non_list_payload(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"unexpected": true}')

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertEqual(runner._cdp_page_urls(port), [])

    def test_wait_for_tenant_management_true_when_present(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = json.dumps([{"type": "page", "url": runner.TENANT_MANAGEMENT + "?tab=accounts"}]).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertTrue(runner._wait_for_tenant_management(port, timeout_seconds=10.0))

    def test_wait_for_tenant_management_false_when_url_not_stable(self):
        import itertools

        # A pre-redirect sighting (the URL appears, then the expired session
        # redirects to login) must not count as having reached tenant-management.
        sequence = itertools.chain(
            [[runner.TENANT_MANAGEMENT]] * 2,
            itertools.repeat([runner.DEVELOPMENT_LOGIN]),
        )
        with patch.object(runner, "_cdp_page_urls", side_effect=sequence), \
             patch.object(runner, "sleep", lambda s: None):
            self.assertFalse(runner._wait_for_tenant_management(1234, timeout_seconds=0.5))

    def test_wait_for_tenant_management_ignores_other_urls(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        payload = json.dumps([{"type": "page", "url": runner.DEVELOPMENT_LOGIN}]).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.assertFalse(runner._wait_for_tenant_management(port, timeout_seconds=1.0))

    def test_wait_for_tenant_management_false_for_dead_port(self):
        port = runner._free_port()
        self.assertFalse(runner._wait_for_tenant_management(port, timeout_seconds=1.0))

    def test_find_live_page_returns_page_at_tenant_management(self):
        page = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT)
        context = _FakeContext(page)
        self.assertIs(runner._find_live_page(context), page)

    def test_find_live_page_ignores_query_string(self):
        page = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT + "?tab=accounts")
        context = _FakeContext(page)
        self.assertIs(runner._find_live_page(context), page)

    def test_find_live_page_returns_none_before_login(self):
        page = types.SimpleNamespace(url=runner.DEVELOPMENT_LOGIN)
        context = _FakeContext(page)
        self.assertIsNone(runner._find_live_page(context))

    def test_find_live_page_adopts_replaced_target(self):
        login_page = types.SimpleNamespace(url=runner.DEVELOPMENT_LOGIN)
        context = _FakeContext(login_page)
        self.assertIsNone(runner._find_live_page(context))
        # Simulate the SSO redirect replacing the CDP target with the
        # post-login page; re-scanning context.pages must adopt the live one.
        live_page = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT)
        context.pages = [live_page]
        self.assertIs(runner._find_live_page(context), live_page)


class LeonardoSessionBridgeTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_bridge_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def test_profile_override_is_persisted(self):
        target = self._temp_path("profile")
        with patch.dict(os.environ, {"SURFACE_LEONARDO_PROFILE_DIR": str(target)}):
            profile_dir, persist = leonardo_profile()
        self.assertEqual(profile_dir, target)
        self.assertTrue(persist)

    def test_profile_default_on_nt_is_persisted(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self._temp_path("base"))}):
            profile_dir, persist = leonardo_profile()
        self.assertTrue(persist)
        self.assertIn("SurfaceOnboarding", str(profile_dir))
        self.assertIn("leonardo-automation", str(profile_dir))

    def test_is_tenant_management_url(self):
        self.assertTrue(_is_tenant_management_url(runner.TENANT_MANAGEMENT))
        self.assertTrue(_is_tenant_management_url(runner.TENANT_MANAGEMENT + "?tab=accounts"))
        self.assertFalse(_is_tenant_management_url(runner.DEVELOPMENT_LOGIN))
        self.assertFalse(_is_tenant_management_url("https://example.com/backoffice/other"))

    def test_classify_session_active(self):
        with patch.object(runner, "_cdp_page_urls", return_value=[runner.TENANT_MANAGEMENT]), \
             patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(_classify_leonardo_session(1234, 1.0), "active")

    def test_classify_session_pre_redirect_sighting_is_expired(self):
        import itertools

        # A single pre-redirect sighting of the tenant-management URL (before
        # the client-side auth redirect sends an expired session to login)
        # must not classify the session as active.
        sequence = itertools.chain(
            [[runner.TENANT_MANAGEMENT]],
            itertools.repeat([runner.DEVELOPMENT_LOGIN]),
        )
        with patch.object(runner, "_cdp_page_urls", side_effect=sequence), \
             patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(_classify_leonardo_session(1234, 0.5), "expired")

    def test_classify_session_expired(self):
        with patch.object(runner, "_cdp_page_urls", return_value=[runner.DEVELOPMENT_LOGIN]), \
             patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(_classify_leonardo_session(1234, 0.2), "expired")

    def test_classify_session_unavailable(self):
        with patch.object(runner, "_cdp_page_urls", return_value=[]), \
             patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(_classify_leonardo_session(1234, 0.2), "unavailable")

    def test_reset_profile_absent(self):
        target = self._temp_path("absent")
        with patch.object(runner, "leonardo_profile", return_value=(target, True)):
            self.assertTrue(reset_leonardo_profile())

    def test_reset_profile_removes_directory(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_reset_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        (profile / "cookie").write_text("x", encoding="utf-8")
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)):
            self.assertTrue(reset_leonardo_profile())
        self.assertFalse(profile.exists())

    def test_reset_runner_record_invalid_reference(self):
        with self.assertRaises(ValueError):
            reset_runner_record("BAD")

    def test_reset_runner_record_absent(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")):
            self.assertTrue(reset_runner_record("CO-0702"))

    def test_reset_runner_record_in_progress_refused(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "started_on": "2026-09-21T10:00:00"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(ValueError):
                reset_runner_record("CO-0702")

    def test_reset_runner_record_verified_refused(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "result": "readback_verified", "completed_on": "2026-09-21T10:00:00"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            with self.assertRaises(ValueError):
                reset_runner_record("CO-0702")

    def test_reset_runner_record_blocked_rearmed(self):
        path = self._temp_path("state.json")
        path.write_text(json.dumps({"CO-0702": {"source_revision": REVISION, "result": "development_login_timeout", "completed_on": "2026-09-21T10:00:00"}}), encoding="utf-8")
        with patch.object(runner, "RUNNER_STATE_PATH", path):
            self.assertTrue(reset_runner_record("CO-0702"))
        self.assertNotIn("CO-0702", json.loads(path.read_text(encoding="utf-8")))

    def test_locate_confirm_button_found(self):
        page = _RPPage({"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {}, "tenant": TENANT, "domain": MAIN_DOMAIN})
        control = _locate_confirm_button(page)
        self.assertIsNotNone(control)
        self.assertEqual(control.count(), 1)

    def test_locate_confirm_button_missing(self):
        class _NoConfirm:
            def get_by_role(self, role, name, exact=True):
                return types.SimpleNamespace(count=lambda: 0)
        self.assertIsNone(_locate_confirm_button(_NoConfirm()))

    def _install_fake_playwright_for_check(self) -> None:
        class _Chromium:
            executable_path = "chrome"

        class _PW:
            chromium = _Chromium()

        class _Ctx:
            def __enter__(self):
                return _PW()

            def __exit__(self, *args):
                return False

        def sync_playwright():
            return _Ctx()

        package = types.ModuleType("playwright")
        sync_api = types.ModuleType("playwright.sync_api")
        sync_api.sync_playwright = sync_playwright
        package.sync_api = sync_api
        previous = {key: sys.modules.get(key) for key in ("playwright", "playwright.sync_api")}

        def restore() -> None:
            for key, value in previous.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

        sys.modules["playwright"] = package
        sys.modules["playwright.sync_api"] = sync_api
        self.addCleanup(restore)

    def _fake_proc(self):
        class _Proc:
            def terminate(self):
                pass

            def wait(self, timeout: float | None = None) -> int:
                return 0

            def kill(self):
                pass
        return _Proc()

    def test_check_session_maps_active(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_session_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), 1234)), patch.object(runner, "_cdp_page_targets", return_value=[]), patch.object(runner, "_cdp_open_tab", return_value="T1"), patch.object(runner, "_cdp_close_tab", return_value=True), \
                patch.object(runner, "_classify_leonardo_session", return_value="active"):
            self.assertEqual(check_leonardo_session(), "leonardo_session_active")

    def test_check_session_maps_expired(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_session_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), 1234)), patch.object(runner, "_cdp_page_targets", return_value=[]), patch.object(runner, "_cdp_open_tab", return_value="T1"), patch.object(runner, "_cdp_close_tab", return_value=True), \
                patch.object(runner, "_classify_leonardo_session", return_value="expired"):
            self.assertEqual(check_leonardo_session(), "leonardo_session_expired")

    def test_check_session_cdp_unavailable(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_session_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), None)):
            self.assertEqual(check_leonardo_session(), "browser_cdp_unavailable")

    def test_bootstrap_session_maps_bootstrapped(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_bootstrap_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), 1234)), patch.object(runner, "_cdp_page_targets", return_value=[]), patch.object(runner, "_cdp_open_tab", return_value="T1"), patch.object(runner, "_cdp_close_tab", return_value=True), \
                patch.object(runner, "_wait_for_tenant_management", return_value=True):
            self.assertEqual(bootstrap_leonardo_session(), "leonardo_session_bootstrapped")

    def test_bootstrap_session_login_timeout(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_bootstrap_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), 1234)), patch.object(runner, "_cdp_page_targets", return_value=[]), patch.object(runner, "_cdp_open_tab", return_value="T1"), patch.object(runner, "_cdp_close_tab", return_value=True), \
                patch.object(runner, "_wait_for_tenant_management", return_value=False):
            self.assertEqual(bootstrap_leonardo_session(), "development_login_timeout")

    def test_bootstrap_session_cdp_unavailable(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_bootstrap_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "profile"
        profile.mkdir()
        self._install_fake_playwright_for_check()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_verified_automation_port", return_value=None), \
                patch.object(runner, "_chrome_executable", return_value=None), \
                patch.object(runner.subprocess, "Popen", return_value=self._fake_proc()), \
                patch.object(runner, "_launch_automation_chrome", return_value=(self._fake_proc(), None)):
            self.assertEqual(bootstrap_leonardo_session(), "browser_cdp_unavailable")


class _FakeCdpServer:
    """A stateful loopback stand-in for Chrome's CDP HTTP endpoints.

    Serves /json/version (with a per-instance browser WebSocket path), /json
    (page targets), PUT /json/new?<url>, and /json/close/<id>. No real
    browser, profile, or external host is involved.
    """

    def __init__(self, test: unittest.TestCase, targets: list[dict] | None = None,
                 ws_path: str = "/devtools/browser/11111111-2222-3333-4444-555555555555"):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        self.ws_path = ws_path
        self.targets = list(targets or [])
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.new_methods: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self, method: str) -> None:
                path = self.path
                if path == "/json/version":
                    port = self.server.server_address[1]
                    self._send(200, json.dumps({
                        "Browser": "Chrome/140.0.0.0",
                        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}{fake.ws_path}",
                    }).encode())
                elif path in ("/json", "/json/list"):
                    self._send(200, json.dumps(fake.targets).encode())
                elif path.startswith("/json/new?"):
                    fake.new_methods.append(method)
                    if method != "PUT":
                        self._send(405, b"Using unsafe HTTP verb GET to invoke /json/new.")
                        return
                    url = path.split("?", 1)[1]
                    target_id = f"NEW{len(fake.opened) + 1:04d}"
                    fake.opened.append(url)
                    target = {"id": target_id, "type": "page", "url": url}
                    fake.targets.append(target)
                    self._send(200, json.dumps(target).encode())
                elif path.startswith("/json/close/"):
                    target_id = path.rsplit("/", 1)[1]
                    fake.closed.append(target_id)
                    fake.targets = [t for t in fake.targets if t.get("id") != target_id]
                    self._send(200, b"Target is closing")
                else:
                    self._send(404, b"")

            def do_GET(self):
                self._route("GET")

            def do_PUT(self):
                self._route("PUT")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        # Cleanups run LIFO: stop serve_forever first, then close the socket.
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def write_active_port(self, profile_dir: Path) -> None:
        (profile_dir / runner.DEVTOOLS_ACTIVE_PORT_FILE).write_text(
            f"{self.port}\n{self.ws_path}\n", encoding="ascii")


class _RecordingProc:
    def __init__(self):
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


def _install_minimal_playwright(test: unittest.TestCase) -> None:
    """Install a fake playwright.sync_api whose chromium has an executable path."""
    class _Chromium:
        executable_path = "chrome"

        def connect_over_cdp(self, endpoint):
            raise AssertionError("connect_over_cdp must not be used by this path")

    class _PW:
        chromium = _Chromium()

    class _Ctx:
        def __enter__(self):
            return _PW()

        def __exit__(self, *args):
            return False

    package = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: _Ctx()
    package.sync_api = sync_api
    previous = {key: sys.modules.get(key) for key in ("playwright", "playwright.sync_api")}

    def restore() -> None:
        for key, value in previous.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value

    sys.modules["playwright"] = package
    sys.modules["playwright.sync_api"] = sync_api
    test.addCleanup(restore)


class DevToolsActivePortTests(unittest.TestCase):
    def _profile(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_devtools_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory

    def _write(self, profile: Path, data: bytes) -> None:
        (profile / runner.DEVTOOLS_ACTIVE_PORT_FILE).write_bytes(data)

    def test_parses_port_and_browser_path(self):
        profile = self._profile()
        self._write(profile, b"9333\n/devtools/browser/abc-123\n")
        self.assertEqual(runner._read_devtools_active_port(profile), (9333, "/devtools/browser/abc-123"))

    def test_parses_crlf_line_endings(self):
        profile = self._profile()
        self._write(profile, b"9333\r\n/devtools/browser/abc-123\r\n")
        self.assertEqual(runner._read_devtools_active_port(profile), (9333, "/devtools/browser/abc-123"))

    def test_missing_file_is_not_running(self):
        self.assertIsNone(runner._read_devtools_active_port(self._profile()))

    def test_corrupt_contents_are_rejected(self):
        profile = self._profile()
        for data in (b"", b"9333\n", b"abc\n/devtools/browser/x\n", b"0\n/devtools/browser/x\n",
                     b"70000\n/devtools/browser/x\n", b"9333\n/devtools/page/x\n",
                     b"9333\n/devtools/browser/../../x\n", b"\xff\xfe9333\n/devtools/browser/x\n",
                     b"9333\n/devtools/browser/x\n" + b"y" * 600):
            self._write(profile, data)
            self.assertIsNone(runner._read_devtools_active_port(profile), data[:40])

    def test_verified_port_for_live_matching_instance(self):
        profile = self._profile()
        server = _FakeCdpServer(self)
        server.write_active_port(profile)
        self.assertEqual(runner._verified_automation_port(profile), server.port)

    def test_stale_file_with_dead_port_is_not_running(self):
        profile = self._profile()
        self._write(profile, f"{runner._free_port()}\n/devtools/browser/stale\n".encode())
        self.assertIsNone(runner._verified_automation_port(profile))
        # Nothing is deleted for a stale entry.
        self.assertTrue((profile / runner.DEVTOOLS_ACTIVE_PORT_FILE).exists())

    def test_port_reused_by_another_instance_is_not_ours(self):
        profile = self._profile()
        server = _FakeCdpServer(self, ws_path="/devtools/browser/other-instance")
        self._write(profile, f"{server.port}\n/devtools/browser/our-instance\n".encode())
        self.assertIsNone(runner._verified_automation_port(profile))


class AutomationTabTests(unittest.TestCase):
    def _tab(self, preexisting=("A",), target_id="OURS") -> runner._AutomationTab:
        return runner._AutomationTab(port=1, target_id=target_id, preexisting=frozenset(preexisting),
                                     profile_dir=Path("unused"), persist=True, chrome_proc=None, reused=True)

    def test_select_returns_only_our_target(self):
        tab = self._tab()
        targets = [{"id": "A", "url": runner.TENANT_MANAGEMENT}, {"id": "OURS", "url": runner.DEVELOPMENT_LOGIN}]
        self.assertEqual(tab.select(targets), [{"id": "OURS", "url": runner.DEVELOPMENT_LOGIN}])

    def test_select_adopts_single_replacement_target(self):
        tab = self._tab()
        targets = [{"id": "A", "url": runner.TENANT_MANAGEMENT}, {"id": "SSO2", "url": runner.TENANT_MANAGEMENT}]
        self.assertEqual([t["id"] for t in tab.select(targets)], ["SSO2"])
        self.assertEqual(tab.target_id, "SSO2")

    def test_select_is_empty_when_replacement_is_ambiguous(self):
        tab = self._tab()
        targets = [{"id": "A", "url": runner.TENANT_MANAGEMENT}, {"id": "X", "url": runner.TENANT_MANAGEMENT},
                   {"id": "Y", "url": runner.TENANT_MANAGEMENT}]
        self.assertEqual(tab.select(targets), [])
        self.assertEqual(tab.target_id, "OURS")

    def test_find_live_page_matches_our_target_id_only(self):
        tab = self._tab()
        other = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT, tid="A")
        ours = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT, tid="OURS")
        context = types.SimpleNamespace(pages=[other, ours])
        with patch.object(runner, "_cdp_page_targets", return_value=[
                {"id": "A", "url": runner.TENANT_MANAGEMENT}, {"id": "OURS", "url": runner.TENANT_MANAGEMENT}]), \
                patch.object(runner, "_page_target_id", side_effect=lambda ctx, page: page.tid):
            self.assertIs(runner._find_live_page(context, tab), ours)

    def test_find_live_page_fails_closed_when_our_page_is_not_attached(self):
        tab = self._tab()
        other = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT, tid="A")
        context = types.SimpleNamespace(pages=[other])
        with patch.object(runner, "_cdp_page_targets", return_value=[
                {"id": "A", "url": runner.TENANT_MANAGEMENT}, {"id": "OURS", "url": runner.TENANT_MANAGEMENT}]), \
                patch.object(runner, "_page_target_id", side_effect=lambda ctx, page: page.tid):
            self.assertIsNone(runner._find_live_page(context, tab))

    def test_open_tab_refuses_non_leonardo_urls(self):
        with patch.object(runner, "_cdp_json", side_effect=AssertionError("no request expected")):
            self.assertIsNone(runner._cdp_open_tab(1, "https://example.com/x"))

    def test_open_and_close_tab_over_http(self):
        server = _FakeCdpServer(self)
        target_id = runner._cdp_open_tab(server.port, runner.TENANT_MANAGEMENT)
        self.assertEqual(target_id, "NEW0001")
        self.assertEqual(server.new_methods, ["PUT"])
        self.assertTrue(runner._cdp_close_tab(server.port, target_id))
        self.assertEqual(server.closed, ["NEW0001"])
        self.assertFalse(runner._cdp_close_tab(server.port, "../json/version"))


class BrowserReuseTests(unittest.TestCase):
    def _profile(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_reuse_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "leonardo-automation"
        profile.mkdir()
        return profile

    def test_reuse_opens_new_tab_and_closes_only_it(self):
        profile = self._profile()
        operator_tab = {"id": "OPERATOR", "type": "page", "url": runner.TENANT_MANAGEMENT}
        server = _FakeCdpServer(self, targets=[operator_tab])
        server.write_active_port(profile)
        _install_minimal_playwright(self)
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(check_leonardo_session(), "leonardo_session_active")
        self.assertEqual(server.opened, [runner.TENANT_MANAGEMENT])
        self.assertEqual(server.closed, ["NEW0001"])
        self.assertEqual([t["id"] for t in server.targets], ["OPERATOR"])

    def test_reuse_ignores_other_tab_at_tenant_management(self):
        # Our tab stays at login while the operator's other tab is at
        # tenant-management: the other tab must not be mistaken for ours.
        profile = self._profile()
        server = _FakeCdpServer(self, targets=[{"id": "OPERATOR", "type": "page", "url": runner.TENANT_MANAGEMENT}])
        server.write_active_port(profile)
        _install_minimal_playwright(self)
        original_open = runner._cdp_open_tab

        def open_then_redirect_to_login(port, url):
            target_id = original_open(port, url)
            for target in server.targets:
                if target["id"] == target_id:
                    target["url"] = runner.DEVELOPMENT_LOGIN
            return target_id

        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(runner, "_cdp_open_tab", side_effect=open_then_redirect_to_login), \
                patch.object(runner, "SESSION_CHECK_SECONDS", 0.3), \
                patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(check_leonardo_session(), "leonardo_session_expired")
        self.assertEqual(server.closed, ["NEW0001"])

    def test_bootstrap_reuses_running_browser(self):
        profile = self._profile()
        server = _FakeCdpServer(self, targets=[{"id": "ANCHOR", "type": "page", "url": "about:blank"}])
        server.write_active_port(profile)
        _install_minimal_playwright(self)
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(bootstrap_leonardo_session(wait_seconds=5), "leonardo_session_bootstrapped")
        self.assertEqual(server.closed, ["NEW0001"])
        self.assertEqual([t["id"] for t in server.targets], ["ANCHOR"])

    def test_launch_when_none_running_uses_os_chosen_port_and_stays_open(self):
        profile = self._profile()
        server = _FakeCdpServer(self)
        proc = _RecordingProc()
        launched: list[list[str]] = []

        def fake_popen(args, **kwargs):
            launched.append(list(args))
            server.targets.append({"id": "ANCHOR", "type": "page", "url": runner.ANCHOR_TAB_URL})
            server.write_active_port(profile)
            return proc

        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"), \
                patch.object(runner.subprocess, "Popen", side_effect=fake_popen), \
                patch.object(runner, "sleep", lambda s: None):
            tab = runner._open_automation_tab(types.SimpleNamespace(), runner.TENANT_MANAGEMENT)
            self.assertFalse(tab.reused)
            self.assertEqual(tab.port, server.port)
            self.assertEqual(tab.preexisting, frozenset({"ANCHOR"}))
            runner._release_automation_tab(tab)
        self.assertEqual(len(launched), 1)
        args = launched[0]
        self.assertIn(f"--user-data-dir={profile}", args)
        self.assertIn("--remote-debugging-port=0", args)
        self.assertNotIn("--remote-debugging-address", " ".join(args))
        self.assertEqual(args[-1], runner.ANCHOR_TAB_URL)
        # Persisted profile: only our tab is closed; the window keeps running.
        self.assertFalse(proc.terminated)
        self.assertEqual(server.closed, ["NEW0001"])
        self.assertTrue(profile.exists())

    def test_stale_active_port_file_leads_to_fresh_launch(self):
        profile = self._profile()
        (profile / runner.DEVTOOLS_ACTIVE_PORT_FILE).write_text(
            f"{runner._free_port()}\n/devtools/browser/stale\n", encoding="ascii")
        server = _FakeCdpServer(self)

        def fake_popen(args, **kwargs):
            server.write_active_port(profile)
            return _RecordingProc()

        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"), \
                patch.object(runner.subprocess, "Popen", side_effect=fake_popen) as popen, \
                patch.object(runner, "sleep", lambda s: None):
            tab = runner._open_automation_tab(types.SimpleNamespace(), runner.TENANT_MANAGEMENT)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(tab.port, server.port)

    def test_launch_without_verified_endpoint_is_cdp_unavailable_and_terminated(self):
        profile = self._profile()
        proc = _RecordingProc()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"), \
                patch.object(runner.subprocess, "Popen", return_value=proc), \
                patch.object(runner, "BROWSER_LAUNCH_SECONDS", 0.2), \
                patch.object(runner, "sleep", lambda s: None):
            with self.assertRaises(RuntimeError) as raised:
                runner._open_automation_tab(types.SimpleNamespace(), runner.TENANT_MANAGEMENT)
        self.assertEqual(str(raised.exception), "browser_cdp_unavailable")
        self.assertTrue(proc.terminated)
        self.assertTrue(profile.exists())

    def test_tab_open_failure_on_reused_browser_never_terminates_it(self):
        profile = self._profile()
        server = _FakeCdpServer(self)
        server.write_active_port(profile)
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(runner, "_cdp_open_tab", return_value=None):
            with self.assertRaises(RuntimeError) as raised:
                runner._open_automation_tab(types.SimpleNamespace(), runner.TENANT_MANAGEMENT)
        self.assertEqual(str(raised.exception), "browser_tab_unavailable")
        self.assertTrue(profile.exists())

    def test_temporary_profile_is_never_reused_and_fully_torn_down(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_temp_profile_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "attended_ce_chrome_x"
        profile.mkdir()
        # Even a live, matching DevToolsActivePort is ignored for a temporary
        # profile: it is always a fresh launch.
        live = _FakeCdpServer(self)
        live.write_active_port(profile)
        proc = _RecordingProc()
        with patch.object(runner, "leonardo_profile", return_value=(profile, False)), \
                patch.object(runner, "_chrome_executable", return_value="chrome.exe"), \
                patch.object(runner.subprocess, "Popen", return_value=proc) as popen, \
                patch.object(runner, "sleep", lambda s: None):
            tab = runner._open_automation_tab(types.SimpleNamespace(), runner.TENANT_MANAGEMENT)
            self.assertFalse(tab.reused)
            self.assertEqual(popen.call_count, 1)
            self.assertNotIn("creationflags", popen.call_args.kwargs)
            runner._release_automation_tab(tab)
        self.assertTrue(proc.terminated)
        self.assertFalse(profile.exists())
        self.assertEqual(live.closed, [])

    def test_attended_page_closes_only_run_tab_on_persisted_profile(self):
        profile = self._profile()
        server = _FakeCdpServer(self, targets=[{"id": "ANCHOR", "type": "page", "url": "about:blank"}])
        server.write_active_port(profile)
        ours = types.SimpleNamespace(url=runner.TENANT_MANAGEMENT)
        anchor = types.SimpleNamespace(url="about:blank")
        closed = {"browser": False}

        class _Browser:
            contexts = [types.SimpleNamespace(pages=[anchor, ours])]

            def close(self):
                closed["browser"] = True

        playwright = types.SimpleNamespace(chromium=types.SimpleNamespace(
            executable_path="chrome", connect_over_cdp=lambda endpoint: _Browser()))
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner.subprocess, "Popen", side_effect=AssertionError("must not launch")), \
                patch.object(runner, "_page_target_id",
                             side_effect=lambda ctx, page: "NEW0001" if page is ours else "ANCHOR"), \
                patch.object(runner, "sleep", lambda s: None):
            with runner._attended_page(playwright) as page:
                self.assertIs(page, ours)
                self.assertEqual(server.closed, [])
        self.assertTrue(closed["browser"])
        self.assertEqual(server.closed, ["NEW0001"])
        self.assertEqual([t["id"] for t in server.targets], ["ANCHOR"])


class CloseAutomationBrowserTests(unittest.TestCase):
    def _profile(self) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="ce_close_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        profile = directory / "leonardo-automation"
        profile.mkdir()
        (profile / "Local State").write_text("x", encoding="utf-8")
        return profile

    def test_not_running_is_reported(self):
        with patch.object(runner, "_send_browser_close", side_effect=AssertionError("nothing to close")):
            self.assertEqual(runner._close_automation_browser_at(self._profile()), "automation_browser_not_running")

    def test_closed_when_endpoint_goes_away(self):
        with patch.object(runner, "_verified_automation_port", return_value=4321), \
                patch.object(runner, "_send_browser_close") as send, \
                patch.object(runner, "_cdp_request", return_value=None), \
                patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(runner._close_automation_browser_at(self._profile()), "automation_browser_closed")
        send.assert_called_once_with(4321)

    def test_still_answering_fails_closed(self):
        with patch.object(runner, "_verified_automation_port", return_value=4321), \
                patch.object(runner, "_send_browser_close"), \
                patch.object(runner, "_cdp_request", return_value=b"{}"), \
                patch.object(runner, "sleep", lambda s: None):
            self.assertEqual(runner._close_automation_browser_at(self._profile(), timeout_seconds=0.2),
                             "automation_browser_close_unavailable")

    def test_close_helper_skips_temporary_profile(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_close_temp_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        with patch.object(runner, "leonardo_profile", return_value=(directory, False)), \
                patch.object(runner, "_close_automation_browser_at", side_effect=AssertionError("not for temp")):
            self.assertEqual(runner.close_automation_browser(), "automation_browser_not_running")
        self.assertFalse(directory.exists())

    def test_reset_closes_running_browser_before_wiping(self):
        profile = self._profile()
        order: list[str] = []

        def close_first(path, *args, **kwargs):
            order.append("close:" + ("present" if path.exists() else "absent"))
            return "automation_browser_closed"

        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_close_automation_browser_at", side_effect=close_first):
            self.assertTrue(reset_leonardo_profile())
        self.assertEqual(order, ["close:present"])
        self.assertFalse(profile.exists())

    def test_reset_fails_closed_when_browser_cannot_be_closed(self):
        profile = self._profile()
        with patch.object(runner, "leonardo_profile", return_value=(profile, True)), \
                patch.object(runner, "_close_automation_browser_at",
                             return_value="automation_browser_close_unavailable"):
            self.assertFalse(reset_leonardo_profile())
        self.assertTrue(profile.exists())

    def test_main_close_browser_flag(self):
        import contextlib
        import io

        out = io.StringIO()
        with patch.object(sys, "argv", ["runner", "--close-browser"]), \
                patch.object(runner, "close_automation_browser", return_value="automation_browser_closed"), \
                contextlib.redirect_stdout(out):
            self.assertEqual(runner.main(), 0)
        self.assertEqual(json.loads(out.getvalue())["result"], "automation_browser_closed")


_RUN_LOG_DIR = None
_ORIGINAL_RUN_LOG_PATH = runner.RUN_LOG_PATH


_ORIGINAL_CHECK_STATE_PATH = runner.CHECK_STATE_PATH
# Safety net for tests that do not patch these paths themselves (for example
# the readback-only failure paths capture diagnostics).
_ORIGINAL_RUNTIME_PATHS = {name: getattr(runner, name)
                           for name in ("DIAGNOSTICS_PATH", "READBACK_PATH", "RUNNER_STATE_PATH")}


def setUpModule():
    # Never write the real integration/ run log, check-state, diagnostics,
    # readback, or runner-state files from tests.
    global _RUN_LOG_DIR
    _RUN_LOG_DIR = Path(tempfile.mkdtemp(prefix="ce_run_log_"))
    runner.RUN_LOG_PATH = _RUN_LOG_DIR / "run_log.json"
    runner.CHECK_STATE_PATH = _RUN_LOG_DIR / "check_state.json"
    runner.DIAGNOSTICS_PATH = _RUN_LOG_DIR / "diagnostics.json"
    runner.READBACK_PATH = _RUN_LOG_DIR / "readbacks.json"
    runner.RUNNER_STATE_PATH = _RUN_LOG_DIR / "runner_state.json"


def tearDownModule():
    runner.RUN_LOG_PATH = _ORIGINAL_RUN_LOG_PATH
    runner.CHECK_STATE_PATH = _ORIGINAL_CHECK_STATE_PATH
    for name, value in _ORIGINAL_RUNTIME_PATHS.items():
        setattr(runner, name, value)
    shutil.rmtree(_RUN_LOG_DIR, ignore_errors=True)


class CheckStateTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="ce_check_")) / "check.json"
        self.addCleanup(shutil.rmtree, self.path.parent, ignore_errors=True)
        patcher = patch.object(runner, "CHECK_STATE_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_means_no_checks(self):
        self.assertEqual(runner.load_check_state(), {})

    def test_start_then_result_keeps_the_start_time(self):
        runner.record_check_start("CO-0728", "duplicate_check", "2026-09-29T15:00:00")
        self.assertEqual(runner.load_check_state()["CO-0728"], {"kind": "duplicate_check", "started_on": "2026-09-29T15:00:00"})
        runner.record_check_result("CO-0728", "duplicate_check", "duplicate_check_clear", "2026-09-29T15:00:40")
        self.assertEqual(runner.load_check_state()["CO-0728"], {
            "kind": "duplicate_check", "result": "duplicate_check_clear",
            "completed_on": "2026-09-29T15:00:40", "started_on": "2026-09-29T15:00:00"})

    def test_a_new_check_replaces_the_previous_one_and_other_cos_are_kept(self):
        runner.record_check_result("CO-0762", "readback", "readback_only_verified", "2026-09-29T14:00:00")
        runner.record_check_result("CO-0728", "duplicate_check", "duplicate_check_found", "2026-09-29T14:00:00")
        runner.record_check_start("CO-0728", "readback", "2026-09-29T15:00:00")
        state = runner.load_check_state()
        self.assertEqual(state["CO-0728"], {"kind": "readback", "started_on": "2026-09-29T15:00:00"})
        self.assertEqual(state["CO-0762"]["result"], "readback_only_verified")

    def test_invalid_records_are_rejected(self):
        with self.assertRaises(ValueError):
            runner.record_check_start("not-a-co", "duplicate_check", "2026-09-29T15:00:00")
        with self.assertRaises(ValueError):
            runner.record_check_start("CO-0728", "create", "2026-09-29T15:00:00")

    def test_corrupt_file_raises(self):
        for content in ("{not json", "[]", json.dumps({"CO-0728": {"kind": "create"}}),
                        json.dumps({"CO-0728": {"kind": "readback", "extra": "x"}})):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    runner.load_check_state()


class RunLogTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="ce_log_")) / "log.json"
        self.addCleanup(shutil.rmtree, self.path.parent, ignore_errors=True)
        patcher = patch.object(runner, "RUN_LOG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_redacts_source_values_and_truncates(self):
        log = runner.RunLog("CO-0679", "create")
        log.add_redactions(TENANT, MAIN_DOMAIN)
        log.event("fill_text", "error", "Company name", f"Timeout filling {TENANT} for {MAIN_DOMAIN.upper()} " + "x" * 500)
        log.write("fill_form_schema_unavailable")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        detail = raw["runs"][-1]["events"][0]["detail"]
        self.assertNotIn(TENANT, detail)
        self.assertNotIn(MAIN_DOMAIN.casefold(), detail.casefold())
        self.assertIn("<value>", detail)
        self.assertLessEqual(len(detail), runner.RUN_LOG_DETAIL_CHARS)
        self.assertEqual(raw["runs"][-1]["result"], "fill_form_schema_unavailable")

    def test_keeps_only_newest_runs(self):
        for index in range(runner.RUN_LOG_MAX_RUNS + 3):
            runner.RunLog("CO-%04d" % index, "create").write("x")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["runs"]), runner.RUN_LOG_MAX_RUNS)
        self.assertEqual(raw["runs"][-1]["reference"], "CO-%04d" % (runner.RUN_LOG_MAX_RUNS + 2))

    def test_browser_response_records_path_and_status_only(self):
        log = runner.RunLog("CO-0679", "create")
        page = _RPPage({"url": runner.TENANT_MANAGEMENT, "tenant": TENANT, "domain": MAIN_DOMAIN})
        log.attach(page)
        response = types.SimpleNamespace(
            url=runner.DEVELOPMENT_ORIGIN + "/api/v1/backoffice/account/add?token=secret", status=400,
            request=types.SimpleNamespace(method="POST"))
        for handler in page.handlers["response"]:
            handler(response)
        event = log.events[-1]
        self.assertEqual(event["outcome"], "400")
        self.assertEqual(event["field"], "/api/v1/backoffice/account/add")
        self.assertNotIn("secret", json.dumps(log.events))

    def test_failed_run_records_the_failing_field(self):
        # A select that cannot be located is logged by label before fail-closed.
        tracker: dict = {}
        e2e = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(e2e.doCleanups)
        # Inject the fake playwright.sync_api so this test does not depend on
        # the real Playwright package being installed.
        page = e2e._install_fake_playwright({"url": runner.TENANT_MANAGEMENT, "tenants": [], "details": {},
                                             "tenant": TENANT, "domain": MAIN_DOMAIN}, tracker)
        original = page.locator
        page.locator = lambda selector: (_RPControl(page, "Country", "other")
                                         if selector == 'select[name="accountCountry"]' else original(selector))
        with patch.object(runner, "ce_fill_source", return_value=e2e._source()), \
                patch.object(runner, "RUNNER_STATE_PATH", self.path.parent / "state.json"), \
                patch.object(runner, "DIAGNOSTICS_PATH", self.path.parent / "diag.json"), \
                patch.object(runner, "_attach_attended_browser", e2e._attach(page, tracker)):
            result = runner.run("CO-0702", REVISION, review_wait_seconds=1)
        self.assertEqual(result, "fill_form_schema_unavailable")
        events = json.loads(self.path.read_text(encoding="utf-8"))["runs"][-1]["events"]
        self.assertIn({"step": "fill_select", "outcome": "not_found", "field": "Country"},
                      [{k: v for k, v in e.items() if k != "t"} for e in events])
        self.assertFalse(page.confirmed)


# --- Golden: the CE-only route is unchanged by the route-contract layer -------

CE_GOLDEN_PLAN = {
    "texts": {
        "Company name": "Sample Company - CE Only",
        "Company primary domain": "company.example",
        "User email domains  (Comma Separated Values)": "pentera.io",
        "First name": "Milton",
        "Last name": "Stevenson",
        "Organization Email": "milton.stevenson+samplecompany@pentera.io",
        "Leaked Credentials scanned domains (Comma Separated Values)": "company.example",
        "Number of assets": "1",
        "Number of domains": "1",
        "Number of subdomains": "1",
    },
    "selects": {
        "Account Type": "Customer",
        "Country": "France",
        "Scanning interval": "None",
        "Leaked Credentials scanning interval": "Weekly",
        "Type": "Prepaid annual subscription",
    },
    "checkboxes": {
        "mfaRequired": True, "scan_now": False,
        "automatedDiscoveryEnabled": False, "subDomainsReconEnabled": False,
        "webDictionaryBruteForceEnabled": False, "webDorkingEnabled": False,
        "fullNucleiScanEnabled": False, "authenticatedTestingEnabled": False,
        "staticOutboundIpEnabled": False, "aiEnabled": False, "multipleAttackStacksEnabled": False,
        "notificationsAllowed": False, "multipleUsersAllowed": False, "apiAccessAllowed": False,
        "phishingEnabled": False, "leakedCredentialsAllowed": True, "provisioningEnabled": True,
        "subDomainsNumberAllowed": True,
    },
    "license_start": date(2026, 9, 29),
    "license_end": date(2027, 9, 27),
}
# Captured from the pre-route-contract runner (HEAD 71bc251) on the fake form.
CE_GOLDEN_EVENTS = [
    ["source_read", "start", "", ""], ["source_read", "ok", "", ""],
    ["license_dates", "ok", "", "start=2026-09-29 end=2027-09-27"],
    ["tenant_search", "200", "", "rows=0 total=0"], ["duplicate_check", "duplicate_clear", "tenant_name", ""],
    ["tenant_search", "200", "", "rows=0 total=0"], ["duplicate_check", "duplicate_clear", "primary_domain", ""],
    ["add_account_open", "clicked", "", ""],
    ["fill_select", "ok", "Account Type", ""], ["fill_select", "ok", "Scanning interval", ""],
    ["fill_select", "ok", "Type", ""], ["advanced_options", "expanded", "", ""],
    ["fill_toggle", "ok", "mfaRequired", ""], ["fill_toggle", "ok", "scan_now", ""],
    ["fill_toggle", "ok", "automatedDiscoveryEnabled", ""], ["fill_toggle", "ok", "subDomainsReconEnabled", ""],
    ["fill_toggle", "ok", "webDictionaryBruteForceEnabled", ""], ["fill_toggle", "ok", "webDorkingEnabled", ""],
    ["fill_toggle", "ok", "fullNucleiScanEnabled", ""], ["fill_toggle", "ok", "authenticatedTestingEnabled", ""],
    ["fill_toggle", "ok", "staticOutboundIpEnabled", ""], ["fill_toggle", "ok", "aiEnabled", ""],
    ["fill_toggle", "ok", "multipleAttackStacksEnabled", ""], ["fill_toggle", "ok", "notificationsAllowed", ""],
    ["fill_toggle", "ok", "multipleUsersAllowed", ""], ["fill_toggle", "ok", "apiAccessAllowed", ""],
    ["fill_toggle", "ok", "phishingEnabled", ""], ["fill_toggle", "ok", "leakedCredentialsAllowed", ""],
    ["fill_toggle", "ok", "provisioningEnabled", ""], ["fill_toggle", "ok", "subDomainsNumberAllowed", ""],
    ["fill_select", "ok", "Country", ""], ["fill_select", "ok", "Leaked Credentials scanning interval", ""],
    ["fill_text", "ok", "Company name", ""], ["fill_text", "ok", "Company primary domain", ""],
    ["fill_text", "ok", "User email domains  (Comma Separated Values)", ""], ["fill_text", "ok", "First name", ""],
    ["fill_text", "ok", "Last name", ""], ["fill_text", "ok", "Organization Email", ""],
    ["fill_text", "ok", "Leaked Credentials scanned domains (Comma Separated Values)", ""],
    ["fill_text", "ok", "Number of assets", ""], ["fill_text", "ok", "Number of domains", ""],
    ["fill_text", "ok", "Number of subdomains", ""],
    ["fill_date", "ok", "license_start", ""], ["fill_date", "ok", "license_end", ""],
    ["verify_form", "ok", "", ""], ["confirm_click", "clicked", "", "account_add_status=200"],
    ["form_close_wait", "closed", "", ""], ["tenant_search", "200", "", "rows=1 total=1"],
    ["post_create_search", "duplicate_found", "attempt=1", ""], ["readback", "api", "", ""],
    ["finish", "readback_verified", "", ""],
]


def _events(log_path: Path) -> list[list[str]]:
    events = json.loads(log_path.read_text(encoding="utf-8"))["runs"][-1]["events"]
    return [[e["step"], e["outcome"], e.get("field", ""), e.get("detail", "")]
            for e in events if not e["step"].startswith("browser_")]


class CeGoldenTests(unittest.TestCase):
    def test_build_ce_only_fill_is_unchanged(self):
        source = BuildCeOnlyFillTests()._source()
        self.assertEqual(build_ce_only_fill(source, date(2026, 9, 29)), CE_GOLDEN_PLAN)
        self.assertEqual(list(build_ce_only_fill(source, date(2026, 9, 29))["texts"]), list(CE_GOLDEN_PLAN["texts"]))
        self.assertEqual(list(build_ce_only_fill(source, date(2026, 9, 29))["checkboxes"]),
                         list(CE_GOLDEN_PLAN["checkboxes"]))

    def test_ce_end_to_end_fake_run_is_unchanged(self):
        case = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(case.doCleanups)
        tracker: dict = {}
        page = case._install_fake_playwright(case._base(), tracker)
        with patch.object(runner, "_run_day", return_value=date(2026, 9, 29)):
            result = case._run_scenario(page, tracker)
        self.assertEqual(result, "readback_verified")
        self.assertEqual(_events(runner.RUN_LOG_PATH), CE_GOLDEN_EVENTS)
        self.assertNotIn("route", json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1])
        # The default route is CE and the ce_only_names output is unchanged.
        self.assertIs(runner.ROUTES[runner.CE_ENGINE], runner.CE_ROUTE)
        self.assertEqual(ce_only_names("Sample Group Ltd."), runner.CeOnlyNames("Sample Group Ltd - CE Only", "sgl"))

    def test_fill_ce_form_alias_is_the_generic_filler(self):
        self.assertIs(runner._fill_ce_form, runner._fill_add_account_form)


# --- Surface-only route (case_1_new_surface_only) ------------------------------

SURFACE_ACCOUNT = "Sample Surface Co."
SURFACE_TENANT = "Sample Surface Co"
SURFACE_MAIN = "surface-sample.example"
SURFACE_ALT = "other-sample.example"
SURFACE_SUB = "app.surface-sample.example"
SURFACE_REVISION = "2026-09-29T08:00:00.000+0000"
RUN_DAY = date(2026, 9, 29)


def _dealhub(product: str, status: str = "Active", start: str = "2026-09-01", end: str = "2029-08-31") -> dict:
    return {"Product_Full_Name__c": product, "DealHub_Status__c": status,
            "DealHub_Subscription_Start_Date__c": start, "DealHub_Subscription_End_Date__c": end}


def _surface_source(*, alternates=(SURFACE_ALT,), subdomains=(SURFACE_SUB,), tier="go", interval="Monthly",
                    baseline=500, addons=0, core_plus=False, revision=SURFACE_REVISION) -> runner.SurfaceFillSource:
    entitlement = runner.SurfaceEntitlement(tier, interval, baseline, addons, None,
                                            date(2026, 9, 1), date(2029, 8, 31), core_plus)
    names = runner.surface_names(SURFACE_ACCOUNT)
    return runner.SurfaceFillSource(
        "CO-0801", revision, "a123456789012345", SURFACE_ACCOUNT, "France", SURFACE_MAIN,
        tuple(alternates), tuple(subdomains), entitlement, names.tenant_name, names.primary_user_alias)


class SurfaceNamingTests(unittest.TestCase):
    def test_tenant_name_has_no_suffix_and_drops_trailing_periods(self):
        self.assertEqual(runner.surface_names(SURFACE_ACCOUNT).tenant_name, SURFACE_TENANT)
        self.assertEqual(runner.surface_names("Sample Company").tenant_name, "Sample Company")

    def test_alias_rule_is_shared_with_ce(self):
        for name in ("Sample Company", "First Main Bank & Trust", "Abcdefghijklmno", SURFACE_ACCOUNT):
            with self.subTest(name=name):
                self.assertEqual(runner.surface_names(name).primary_user_alias,
                                 ce_only_names(name).primary_user_alias)

    def test_blank_name_raises(self):
        with self.assertRaises(ValueError):
            runner.surface_names("  ")


class SurfaceEntitlementTests(unittest.TestCase):
    def _select(self, rows, run_day=RUN_DAY):
        return runner.select_surface_entitlement(rows, run_day)

    def _code(self, rows, run_day=RUN_DAY) -> str:
        with self.assertRaises(runner.SurfaceSourceError) as caught:
            self._select(rows, run_day)
        return str(caught.exception)

    def test_tiers_map_to_scanning_intervals(self):
        cases = {
            "Pentera Surface Go - 500 Subdomains": ("go", "Monthly", 500),
            "Pentera Surface Prime - 1000 Subdomains": ("prime", "Weekly", 1000),
            "Pentera Surface Prime": ("prime", "Weekly", 1000),
            "Pentera Surface Software - Essentials - 150 Sub-Domains & 1 Domains": ("essentials", "Monthly", 150),
            "Pentera Surface Software Professional - 150 Sub-Domains & 3 Domains": ("professional", "Monthly", 150),
            "Pentera Surface Software Enterprise - Up to 650 Sub-Domains & 5 Domains": ("enterprise", "Weekly", 650),
        }
        for product, (tier, interval, subdomains) in cases.items():
            with self.subTest(product=product):
                entitlement = self._select([_dealhub(product)])
                self.assertEqual((entitlement.tier, entitlement.scanning_interval, entitlement.licensed_subdomains),
                                 (tier, interval, subdomains))
                self.assertFalse(entitlement.core_plus_present)

    def test_subdomain_addons_are_added_and_domain_addons_are_not(self):
        entitlement = self._select([
            _dealhub("Pentera Surface Software Enterprise - Up to 650 Sub-Domains & 5 Domains"),
            _dealhub("Pentera Surface Software Enterprise - Sub-Domain Add-on - 1 Bulks of 400 Sub-Domains"),
            _dealhub("Pentera Surface Add-on - Additional 250 Subdomains"),
            _dealhub("Pentera Surface Software Enterprise - Domain Add-on - 1 bulks of 10 Domains"),
            _dealhub("Pentera Surface Add-on - Additional 999 Subdomains", status="Expired"),
        ])
        self.assertEqual((entitlement.baseline_subdomains, entitlement.addon_subdomains,
                          entitlement.licensed_subdomains, entitlement.product_domains), (650, 650, 1300, 5))

    def test_pending_window_boundaries(self):
        self.assertEqual(runner.SURFACE_PENDING_START_WINDOW_DAYS, 14)
        product = "Pentera Surface Go - 500 Subdomains"
        for offset, counts in ((-30, True), (0, True), (13, True), (14, True), (15, False)):
            start = (RUN_DAY + runner.timedelta(days=offset)).isoformat()
            rows = [_dealhub(product, status="Pending", start=start, end="2029-09-30")]
            with self.subTest(offset=offset):
                if counts:
                    self.assertEqual(self._select(rows).baseline_subdomains, 500)
                else:
                    self.assertEqual(self._code(rows), "surface_baseline_unavailable")

    def test_active_is_unaffected_by_a_future_start(self):
        rows = [_dealhub("Pentera Surface Go - 500 Subdomains", status="Active", start="2027-01-01", end="2029-12-31")]
        self.assertEqual(self._select(rows).subscription_start, date(2027, 1, 1))

    def test_real_prime_pending_shape_counts(self):
        rows = [_dealhub("Pentera Surface Prime - 1000 Subdomains", status="Pending",
                         start="2026-10-01", end="2029-09-30")]
        entitlement = self._select(rows, date(2026, 9, 29))
        self.assertEqual((entitlement.tier, entitlement.scanning_interval, entitlement.licensed_subdomains),
                         ("prime", "Weekly", 1000))

    def test_expired_baselines_are_ignored(self):
        rows = [_dealhub("Pentera Surface Software - Essentials - 150 Sub-Domains & 1 Domains", status="Expired"),
                _dealhub("Pentera Surface Go - 500 Subdomains")]
        self.assertEqual(self._select(rows).tier, "go")
        self.assertEqual(self._code(rows[:1]), "surface_baseline_unavailable")

    def test_two_counted_baselines_are_ambiguous(self):
        rows = [_dealhub("Pentera Surface Go - 500 Subdomains"), _dealhub("Pentera Surface Prime - 1000 Subdomains")]
        self.assertEqual(self._code(rows), "surface_baseline_ambiguous")

    def test_fail_closed_codes(self):
        self.assertEqual(self._code([]), "surface_baseline_unavailable")
        self.assertEqual(self._code([_dealhub("Pentera Surface - 1000 Subdomains")]), "surface_tier_unknown")
        self.assertEqual(self._code([_dealhub("Pentera Surface Ultra - 5 Subdomains")]), "surface_product_unrecognized")
        self.assertEqual(self._code([_dealhub("")]), "surface_product_unrecognized")
        self.assertEqual(self._code([_dealhub("Pentera Surface Go - 500 Subdomains", status="")]),
                         "surface_subscription_invalid")
        self.assertEqual(self._code([_dealhub("Pentera Surface Go - 500 Subdomains", start="x")]),
                         "surface_subscription_invalid")
        self.assertEqual(self._code([_dealhub("Pentera Surface Go - 500 Subdomains", end="2026-08-01")]),
                         "surface_subscription_invalid")

    def test_security_validation_advisor_rows_are_not_baselines(self):
        rows = [_dealhub("Security Validation Advisor - Premium Incl. Surface"),
                _dealhub("Pentera Surface Go - 500 Subdomains")]
        self.assertEqual(self._select(rows).tier, "go")

    def test_core_plus_baselines_do_not_block_and_set_the_flag(self):
        for core in ("Pentera Core Plus Commercial - 500 End Points", "Pentera Core Plus Enterprise - 2000 End Points"):
            with self.subTest(core=core):
                entitlement = self._select([_dealhub("Pentera Surface Go - 500 Subdomains"), _dealhub(core)])
                self.assertTrue(entitlement.core_plus_present)
                self.assertEqual(entitlement.tier, "go")

    def test_core_plus_bulk_or_expired_rows_do_not_set_the_flag(self):
        for row in (_dealhub("Pentera Core Plus Commercial - Bulk 100 End Points"),
                    _dealhub("Pentera Core Plus Commercial - Additional 100 End Points"),
                    _dealhub("Pentera Core Plus Commercial - 500 End Points", status="Expired")):
            with self.subTest(product=row["Product_Full_Name__c"], status=row["DealHub_Status__c"]):
                entitlement = self._select([_dealhub("Pentera Surface Go - 500 Subdomains"), row])
                self.assertFalse(entitlement.core_plus_present)
        self.assertTrue(runner.is_core_plus_baseline_row("pentera core plus commercial - 500 end points"))
        self.assertFalse(runner.is_core_plus_baseline_row(None))


class SurfaceDomainTests(unittest.TestCase):
    def test_alternates_are_split_into_roots_and_subdomains(self):
        main, roots, subdomains = runner.classify_surface_domains(
            "Surface-Sample.Example", f"{SURFACE_ALT}, {SURFACE_SUB};{SURFACE_MAIN}\n{SURFACE_ALT}")
        self.assertEqual((main, roots, subdomains), (SURFACE_MAIN, (SURFACE_ALT,), (SURFACE_SUB,)))

    def test_empty_alternates(self):
        for value in (None, "", " , ;"):
            with self.subTest(value=value):
                self.assertEqual(runner.classify_surface_domains(SURFACE_MAIN, value), (SURFACE_MAIN, (), ()))

    def test_main_domain_must_be_a_registrable_root(self):
        for value in (SURFACE_SUB, "co.uk", "10.0.0.0/24", "", None, "*.surface-sample.example", "a b.example",
                      f"{SURFACE_MAIN}, {SURFACE_ALT}"):
            with self.subTest(value=value), self.assertRaises(runner.SurfaceSourceError) as caught:
                runner.classify_surface_domains(value, "")
            self.assertEqual(str(caught.exception), "surface_main_domain_invalid")

    def test_networks_fail_closed(self):
        for value in ("10.0.0.0/24", "192.0.2.10", f"{SURFACE_ALT}, 2001:db8::/32"):
            with self.subTest(value=value), self.assertRaises(runner.SurfaceSourceError) as caught:
                runner.classify_surface_domains(SURFACE_MAIN, value)
            self.assertEqual(str(caught.exception), "surface_networks_not_supported")

    def test_wildcards_and_malformed_entries_fail_closed(self):
        for value in ("*.other-sample.example", "user@other-sample.example", "not a domain", "co.uk", "-bad-.example"):
            with self.subTest(value=value), self.assertRaises(runner.SurfaceSourceError) as caught:
                runner.classify_surface_domains(SURFACE_MAIN, value)
            self.assertEqual(str(caught.exception), "surface_domains_invalid")


class SurfaceFillSourceTests(unittest.TestCase):
    CO = {"Name": "CO-0801", "LastModifiedDate": SURFACE_REVISION, "Account__c": "a123456789012345",
          "Account_Name__c": SURFACE_ACCOUNT, "Account_Country__c": "France", "Main_Domain__c": SURFACE_MAIN,
          "Alternate_Domains__c": f"{SURFACE_ALT}, {SURFACE_SUB}", "Onboarding_Product__c": "Surface",
          "Onboarding_Type__c": "New Product Onboarding"}

    def _patch(self, co_records, subscription_records=None):
        responses = [CeFillSourceTests._completed(CeFillSourceTests._sf_json(co_records))]
        if subscription_records is not None:
            responses.append(CeFillSourceTests._completed(CeFillSourceTests._sf_json(subscription_records)))
        return patch.object(runner.subprocess, "run", side_effect=responses)

    def test_valid_read(self):
        with self._patch([self.CO], [_dealhub("Pentera Surface Go - 500 Subdomains"),
                                     _dealhub("Pentera Core Plus Commercial - 500 End Points")]), \
                patch.object(runner, "_run_day", return_value=RUN_DAY):
            source = runner.surface_fill_source("CO-0801")
        self.assertEqual((source.tenant_name, source.main_domain, source.alternate_domains, source.subdomains),
                         (SURFACE_TENANT, SURFACE_MAIN, (SURFACE_ALT,), (SURFACE_SUB,)))
        self.assertEqual((source.listed_domains, source.number_of_domains, source.entitlement.licensed_subdomains),
                         (2, 500, 500))  # owner rule 2026-09-30: domains = licensed subdomains
        self.assertEqual(source.primary_user_alias, ce_only_names(SURFACE_ACCOUNT).primary_user_alias)
        self.assertTrue(source.core_plus_present)

    def test_listed_domains_beyond_the_licensed_subdomains_fail_closed(self):
        # Owner rule 2026-09-30: Number of domains = licensed subdomains; the
        # CO's own domains (main + alternates) must fit in it.
        tiny = runner.SurfaceEntitlement(tier="go", scanning_interval="Monthly", baseline_subdomains=1,
                                         addon_subdomains=0, product_domains=None,
                                         subscription_start=date(2026, 9, 1), subscription_end=date(2027, 8, 31))
        with self._patch([self.CO], [_dealhub("Pentera Surface Go - 500 Subdomains")]), \
                patch.object(runner, "select_surface_entitlement", return_value=tiny), \
                self.assertRaises(runner.SurfaceSourceError) as caught:
            runner.surface_fill_source("CO-0801")
        self.assertEqual(str(caught.exception), "surface_domains_exceed_license")

    def test_route_gate_requires_exact_values_before_the_subscription_read(self):
        for product, onboarding_type in (("Credential Exposure", "New Product Onboarding"),
                                         ("Surface & Credential Exposure", "New Product Onboarding"),
                                         ("Surface", "Renewal"), ("surface", "New Product Onboarding"),
                                         (None, None)):
            record = dict(self.CO, Onboarding_Product__c=product, Onboarding_Type__c=onboarding_type)
            with self.subTest(product=product, type=onboarding_type), self._patch([record]) as run_mock:
                with self.assertRaisesRegex(RuntimeError, "surface_route_mismatch"):
                    runner.surface_fill_source("CO-0801")
                self.assertEqual(run_mock.call_count, 1)

    def test_ce_route_still_rejects_a_surface_co(self):
        record = dict(CeFillSourceTests.CO_RECORD, Onboarding_Product__c="Surface")
        with patch.object(runner.subprocess, "run", side_effect=[
                CeFillSourceTests._completed(CeFillSourceTests._sf_json([record]))]):
            with self.assertRaisesRegex(RuntimeError, "ce_route_mismatch"):
                ce_fill_source("CO-0702")

    def test_source_failures_fail_closed(self):
        for field, value, code in (("Account_Country__c", None, "surface_source_unavailable"),
                                   ("Account__c", "bad", "surface_source_unavailable"),
                                   ("Main_Domain__c", SURFACE_SUB, "surface_main_domain_invalid"),
                                   ("Alternate_Domains__c", "10.1.0.0/16", "surface_networks_not_supported")):
            record = dict(self.CO, **{field: value})
            with self.subTest(field=field), self._patch([record], [_dealhub("Pentera Surface Go - 500 Subdomains")]):
                with self.assertRaisesRegex(RuntimeError, code):
                    runner.surface_fill_source("CO-0801")
        with self._patch([self.CO, self.CO]):
            with self.assertRaisesRegex(RuntimeError, "surface_source_unavailable"):
                runner.surface_fill_source("CO-0801")
        with self.assertRaisesRegex(RuntimeError, "invalid_co_reference"):
            runner.surface_fill_source("BAD")


class BuildSurfaceOnlyFillTests(unittest.TestCase):
    def test_fill_plan_matches_the_owner_contract(self):
        plan = runner.build_surface_only_fill(_surface_source(addons=250), RUN_DAY)
        self.assertEqual(plan["texts"], {
            "Company name": SURFACE_TENANT,
            "Company primary domain": SURFACE_MAIN,
            "Alternate Domains (Comma Separated Values)": SURFACE_ALT,
            "SubDomains (Comma Separated Values)": SURFACE_SUB,
            "User email domains  (Comma Separated Values)": "pentera.io",
            "First name": "Milton",
            "Last name": "Stevenson",
            "Organization Email": "milton.stevenson+ssc@pentera.io",
            "Number of assets": "10000",
            "Number of domains": "750",  # owner rule 2026-09-30: = licensed subdomains
            "Number of subdomains": "750",
        })
        self.assertEqual(plan["selects"], {"Account Type": "Customer", "Country": "France",
                                           "Scanning interval": "Monthly", "Type": "Prepaid annual subscription"})
        # Scan now only exists while Scanning interval is None (live probe
        # 2026-09-29); with the Monthly schedule it is verified absent.
        self.assertEqual(plan["absent_checkboxes"], ("scan_now",))
        self.assertEqual(plan["checkboxes"], {
            "mfaRequired": True,
            "automatedDiscoveryEnabled": False, "subDomainsReconEnabled": True,
            "webDictionaryBruteForceEnabled": True, "webDorkingEnabled": False,
            "fullNucleiScanEnabled": True, "authenticatedTestingEnabled": False,
            "staticOutboundIpEnabled": False, "aiEnabled": False, "multipleAttackStacksEnabled": False,
            "notificationsAllowed": True, "multipleUsersAllowed": True, "apiAccessAllowed": True,
            "phishingEnabled": False, "leakedCredentialsAllowed": False, "provisioningEnabled": True,
            "subDomainsNumberAllowed": True,
        })
        self.assertEqual(plan["advanced_texts"], {"Maximum scan Duration (hours)": "90"})
        self.assertEqual(plan["blank_texts"], ("Networks (Comma Separated Values)", "Phone number", "Job title"))
        self.assertTrue(plan["operator_account_empty"])
        self.assertEqual((plan["license_start"], plan["license_end"]), (RUN_DAY, date(2027, 8, 31)))
        # The two form-disabled toggles are never touched; LC interval/domains are not filled.
        for key in ("subDomainsMultipleAttackStacksEnabled", "webAiAttackerEnabled"):
            self.assertNotIn(key, plan["checkboxes"])
        self.assertNotIn("Leaked Credentials scanning interval", plan["selects"])
        self.assertNotIn("Leaked Credentials scanned domains (Comma Separated Values)", plan["texts"])

    def test_empty_alternates_and_subdomains_are_verified_blank(self):
        plan = runner.build_surface_only_fill(_surface_source(alternates=(), subdomains=()), RUN_DAY)
        self.assertNotIn("Alternate Domains (Comma Separated Values)", plan["texts"])
        self.assertNotIn("SubDomains (Comma Separated Values)", plan["texts"])
        self.assertEqual(plan["blank_texts"][:2], ("Alternate Domains (Comma Separated Values)",
                                                   "SubDomains (Comma Separated Values)"))
        self.assertEqual(plan["texts"]["Number of domains"], plan["texts"]["Number of subdomains"])

    def test_prime_interval_and_multiple_values_csv(self):
        source = _surface_source(alternates=("a-sample.example", "b-sample.example"),
                                 subdomains=("x.surface-sample.example", "y.surface-sample.example"),
                                 tier="prime", interval="Weekly", baseline=1000)
        plan = runner.build_surface_only_fill(source, RUN_DAY)
        self.assertEqual(plan["selects"]["Scanning interval"], "Weekly")
        self.assertEqual(plan["texts"]["Alternate Domains (Comma Separated Values)"], "a-sample.example, b-sample.example")
        self.assertEqual(plan["texts"]["SubDomains (Comma Separated Values)"],
                         "x.surface-sample.example, y.surface-sample.example")
        self.assertEqual((plan["texts"]["Number of domains"], plan["texts"]["Number of subdomains"]), ("1000", "1000"))

    def test_core_plus_on_the_account_keeps_leaked_credentials_off(self):
        plan = runner.build_surface_only_fill(_surface_source(core_plus=True), RUN_DAY)
        self.assertIs(plan["checkboxes"]["leakedCredentialsAllowed"], False)

    def test_api_access_is_on_by_default_for_every_surface_co(self):
        self.assertFalse(runner.SURFACE_API_ACCESS_REQUIRES_CORE_PLUS)
        for core_plus in (False, True):
            with self.subTest(core_plus=core_plus):
                plan = runner.build_surface_only_fill(_surface_source(core_plus=core_plus), RUN_DAY)
                self.assertIs(plan["checkboxes"]["apiAccessAllowed"], True)

    def test_api_access_can_follow_core_plus_behind_one_constant(self):
        with patch.object(runner, "SURFACE_API_ACCESS_REQUIRES_CORE_PLUS", True):
            self.assertIs(runner.build_surface_only_fill(_surface_source(core_plus=True), RUN_DAY)
                          ["checkboxes"]["apiAccessAllowed"], True)
            self.assertIs(runner.build_surface_only_fill(_surface_source(core_plus=False), RUN_DAY)
                          ["checkboxes"]["apiAccessAllowed"], False)

    def test_expired_license_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "surface_license_dates_unavailable"):
            runner.build_surface_only_fill(_surface_source(), date(2027, 8, 31))

    def test_scope_summary_is_counts_only(self):
        summary = runner.surface_scope_summary(_surface_source(addons=250, core_plus=True), RUN_DAY)
        self.assertEqual(summary, {
            "tier": "go", "scanning_interval": "Monthly", "main_domains": 1, "alternate_root_domains": 1,
            "requested_subdomains": 1, "number_of_domains": 750, "baseline_subdomains": 500,
            "addon_subdomains": 250, "licensed_subdomains": 750, "product_domains": None, "assets": 10000,
            "license_start": "2026-09-29", "license_end": "2027-08-31", "large_scope": True,
            "core_plus_present": True,
        })
        self.assertNotIn(SURFACE_MAIN, json.dumps(summary))


class SurfaceApiReadbackTests(unittest.TestCase):
    def test_scan_started_is_accepted_only_for_the_surface_route(self):
        result = runner.TenantSearchResult([{"accountName": SURFACE_TENANT, "accountDomain": SURFACE_MAIN,
                                             "id": SURFACE_ID, "accountUuid": UUID,
                                             "lastReconScan": "2026-09-29T10:00:00Z"}], 1)
        self.assertIsNone(runner._api_readback(result, SURFACE_TENANT, SURFACE_MAIN))
        self.assertEqual(runner._api_readback(result, SURFACE_TENANT, SURFACE_MAIN, allow_scan_started=True),
                         (SURFACE_ID, UUID, "Account Scanning"))
        idle = runner.TenantSearchResult([dict(result.rows[0], lastReconScan=None)], 1)
        self.assertEqual(runner._api_readback(idle, SURFACE_TENANT, SURFACE_MAIN, allow_scan_started=True),
                         (SURFACE_ID, UUID, "No scan started"))
        self.assertTrue(runner.SURFACE_ROUTE.allow_scan_started)
        self.assertFalse(runner.CE_ROUTE.allow_scan_started)


class SurfaceRunEndToEndTests(unittest.TestCase):
    def _temp_path(self, name: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="surface_run_test_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return directory / name

    def _scenario(self, **extra):
        scenario = {"url": runner.TENANT_MANAGEMENT, "tenants": [], "empty_state_row": True, "create_on_confirm": True,
                    "details": {"Surface Account ID": SURFACE_ID, "Account UUID": UUID,
                                "Account Scanning": "Account Scanning"},
                    "tenant": SURFACE_TENANT, "domain": SURFACE_MAIN}
        scenario.update(extra)
        return scenario

    def _run(self, scenario, source=None, **run_kwargs):
        helper = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(helper.doCleanups)
        tracker: dict = {}
        page = helper._install_fake_playwright(scenario, tracker)
        self.readbacks = self._temp_path("readbacks.json")
        self.state = self._temp_path("state.json")
        with patch.object(runner, "surface_fill_source", return_value=source or _surface_source()), \
                patch.object(runner, "ce_fill_source", side_effect=AssertionError("CE source must not be read")), \
                patch.object(runner, "_run_day", return_value=RUN_DAY), \
                patch.object(runner, "RUNNER_STATE_PATH", self.state), \
                patch.object(runner, "READBACK_PATH", self.readbacks), \
                patch.object(runner, "DIAGNOSTICS_PATH", self._temp_path("diag.json")), \
                patch.object(runner, "_attach_attended_browser", helper._attach(page, tracker)):
            result = runner.run("CO-0801", SURFACE_REVISION, review_wait_seconds=1, route=runner.SURFACE_ENGINE,
                                **run_kwargs)
        self.tracker = tracker
        return result, page

    @staticmethod
    def _events():
        return json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]["events"]

    def _confirm_enabled_when(self, condition):
        original = _RPControl.is_enabled
        return patch.object(_RPControl, "is_enabled",
                            lambda control: condition(control.page) if control.kind == "confirm" else original(control))

    def test_dry_run_with_disabled_confirm_fails_and_cancels(self):
        # Live 2026-09-30 (CO-0649): the fill verified but Confirm stayed
        # disabled. A dry run must now catch that instead of reporting success.
        with self._confirm_enabled_when(lambda page: False):
            result, page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_confirm_not_enabled")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        self.assertFalse(self.state.exists())  # the one-time gate is untouched
        steps = [(e["step"], e["outcome"]) for e in self._events()]
        self.assertIn(("confirm_enable", "disabled"), steps)

    def _accepts_start(self, accepted: date):
        return self._confirm_enabled_when(lambda page: page.filled.get("Start date") == accepted.isoformat())

    def test_create_retries_the_start_one_day_earlier_when_confirm_stays_disabled(self):
        # Live 2026-09-30 (CO-0649): Leonardo kept Confirm disabled with the
        # run day as start and enabled it with the day before (operator check).
        earlier = RUN_DAY - timedelta(days=1)
        with self._accepts_start(earlier):
            result, page = self._run(self._scenario())
        self.assertEqual(result, "readback_verified")
        self.assertTrue(page.confirmed)
        self.assertEqual(page.filled["Start date"], earlier.isoformat())
        plan = runner.build_surface_only_fill(_surface_source(), RUN_DAY)
        self.assertEqual(page.filled["Expiration date"], plan["license_end"].isoformat())  # unchanged
        steps = [(e["step"], e["outcome"]) for e in self._events()]
        self.assertIn(("license_start_fallback", "confirm_enabled"), steps)

    def test_run_day_start_is_kept_when_confirm_enables(self):
        with self._accepts_start(RUN_DAY):
            result, page = self._run(self._scenario())
        self.assertEqual(result, "readback_verified")
        self.assertEqual(page.filled["Start date"], RUN_DAY.isoformat())
        self.assertNotIn("license_start_fallback", [e["step"] for e in self._events()])

    def test_fallback_is_bounded_to_one_day_and_fails_closed(self):
        with self._accepts_start(RUN_DAY - timedelta(days=2)):
            result, page = self._run(self._scenario())
        self.assertEqual(result, "confirm_button_not_enabled")
        self.assertFalse(page.confirmed)
        self.assertIn(("license_start_fallback", "still_disabled"),
                      [(e["step"], e["outcome"]) for e in self._events()])

    def test_dry_run_uses_the_same_start_fallback(self):
        with self._accepts_start(RUN_DAY - timedelta(days=1)):
            result, page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_fill_verified")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)

    def test_dry_run_requires_and_logs_an_enabled_confirm(self):
        result, page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_fill_verified")
        self.assertFalse(page.confirmed)
        self.assertIn(("confirm_enable", "enabled"), [(e["step"], e["outcome"]) for e in self._events()])

    def test_diagnose_finds_the_single_blocking_change_and_restores_it(self):
        blocked_by_duration = self._confirm_enabled_when(
            lambda page: page.filled.get(runner.SURFACE_MAX_SCAN_DURATION_LABEL) == "24")
        with blocked_by_duration:
            result, page = self._run(self._scenario(), diagnose=True)
        self.assertEqual(result, "diagnose_confirm_blocker_found")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        self.assertFalse(self.state.exists())
        probes = {e["field"]: e["outcome"] for e in self._events() if e["step"] == "diagnose_probe"}
        self.assertEqual(probes["max_scan_duration_24"], "enables_confirm")
        self.assertEqual(probes["alternate_domains_blank"], "no_change")
        self.assertEqual(probes["scanning_interval_none"], "no_change")
        self.assertNotIn("undo_failed", probes.values())
        # Every probe put the planned value back before the next one.
        self.assertEqual(page.filled[runner.SURFACE_MAX_SCAN_DURATION_LABEL], "90")
        plan = runner.build_surface_only_fill(_surface_source(), RUN_DAY)
        self.assertEqual(page.filled[runner.ALTERNATE_DOMAINS_LABEL], plan["texts"][runner.ALTERNATE_DOMAINS_LABEL])
        self.assertEqual(page.selected["Scanning interval"], plan["selects"]["Scanning interval"])

    def test_diagnose_without_a_single_blocking_change_reports_unknown(self):
        turned_on = []
        original_click = _RPCheckboxControl.click

        def click(control, **kwargs):
            original_click(control, **kwargs)
            if control.page.checkbox_states[control.key]:
                turned_on.append(control.key)

        with self._confirm_enabled_when(lambda page: False), patch.object(_RPCheckboxControl, "click", click):
            result, page = self._run(self._scenario(), diagnose=True)
        self.assertEqual(result, "diagnose_confirm_blocker_unknown")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        probes = {e["field"]: e["outcome"] for e in self._events() if e["step"] == "diagnose_probe"}
        for name in ("number_of_assets_1", "company_name_plain", "organization_email_plain",
                     "toggle_off:apiAccessAllowed", "toggle_off:fullNucleiScanEnabled"):
            self.assertEqual(probes.get(name), "no_change", name)
        # No CE on a Surface-only tenant: no probe ever turned these ON.
        self.assertNotIn("leakedCredentialsAllowed", turned_on)
        self.assertNotIn("phishingEnabled", turned_on)
        self.assertFalse(page.checkbox_states["leakedCredentialsAllowed"])
        self.assertNotIn("undo_failed", probes.values())  # every single-change probe was undone
        # The cumulative groups then ran (left applied; the form is cancelled).
        steps = [(e["step"], e["outcome"]) for e in self._events()]
        self.assertIn(("diagnose_cumulative", "still_disabled"), steps)

    def test_diagnose_cumulative_groups_find_a_combined_blocker_without_lc(self):
        # Confirm enables only once two fields change together: no single
        # probe finds it, the cumulative CE-like groups do (never touching LC).
        def combined(page):
            return (page.filled.get(runner.ALTERNATE_DOMAINS_LABEL) == ""
                    and page.filled.get("Number of assets") == "1")
        with self._confirm_enabled_when(combined):
            result, page = self._run(self._scenario(), diagnose=True)
        self.assertEqual(result, "diagnose_confirm_blocker_combined")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        self.assertFalse(page.checkbox_states["leakedCredentialsAllowed"])
        self.assertFalse(page.checkbox_states["phishingEnabled"])
        groups = {e["field"]: e["outcome"] for e in self._events() if e["step"] == "diagnose_group"}
        self.assertEqual(groups["numbers_ce_like"], "no_change")
        self.assertEqual(groups["alternate_domains_blank"], "enables_confirm")

    def test_lc_prefill_probe_finds_the_blocker_and_leaves_lc_off(self):
        # Live 2026-09-30 hypothesis: Confirm needs the dormant LC domain even
        # with Leaked Credentials OFF. The approved probe sets it and ends OFF.
        def needs_dormant_lc_domain(page):
            return (page.filled.get(runner.LC_SCANNED_DOMAINS_LABEL) == SURFACE_MAIN
                    and not page.checkbox_states["leakedCredentialsAllowed"])
        with self._confirm_enabled_when(needs_dormant_lc_domain):
            result, page = self._run(self._scenario(), diagnose=True, lc_prefill_probe=True)
        self.assertEqual(result, "diagnose_confirm_blocker_found")
        self.assertFalse(page.confirmed)
        self.assertTrue(page.cancelled)
        self.assertFalse(page.checkbox_states["leakedCredentialsAllowed"])
        self.assertFalse(page.checkbox_states["phishingEnabled"])
        steps = [(e["step"], e["outcome"], e.get("field", "")) for e in self._events()]
        self.assertIn(("diagnose_lc_prefill", "lc_off_verified", ""), steps)
        self.assertIn(("diagnose_probe", "enables_confirm", "lc_domains_prefill_lc_off"), steps)

    def test_diagnose_timeline_and_alternate_domain_searches(self):
        with self._confirm_enabled_when(lambda page: False), \
                patch.object(_RPPage, "evaluate", lambda self, script, *a:
                             "confirm=disabled form_hooks=4:true,5:false" if script == runner.TIMELINE_SNAPSHOT_JS
                             else None, create=True):
            result, page = self._run(self._scenario(tenants=[_RPRow("Other Co", SURFACE_ALT)]), diagnose=True)
        self.assertEqual(result, "diagnose_confirm_blocker_unknown")
        self.assertFalse(page.confirmed)
        events = self._events()
        snapshots = [e for e in events if e["step"] == "timeline"]
        self.assertTrue(snapshots)
        self.assertIn("fill_text", {e["field"] for e in snapshots})
        self.assertEqual(snapshots[0]["detail"], "confirm=disabled form_hooks=4:true,5:false")
        alt = [e for e in events if e["step"] == "diagnose_alt_domain_search"]
        self.assertEqual([(e["field"], e["outcome"]) for e in alt], [("alternate_1", "duplicate_found")])
        self.assertNotIn(SURFACE_ALT, runner.RUN_LOG_PATH.read_text(encoding="utf-8"))

    def test_normal_runs_take_no_timeline_snapshots(self):
        with patch.object(_RPPage, "evaluate", lambda self, script, *a:
                          (_ for _ in ()).throw(AssertionError("no snapshot")) if script == runner.TIMELINE_SNAPSHOT_JS
                          else None, create=True):
            result, _page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_fill_verified")
        self.assertFalse([e for e in self._events() if e["step"] in ("timeline", "browser_request")])

    def test_diagnose_without_the_flag_never_touches_lc(self):
        touched = []
        original_click = _RPCheckboxControl.click

        def click(control, **kwargs):
            touched.append(control.key)
            original_click(control, **kwargs)

        with self._confirm_enabled_when(lambda page: False), patch.object(_RPCheckboxControl, "click", click):
            self._run(self._scenario(), diagnose=True)
        self.assertNotIn("leakedCredentialsAllowed", touched)

    def test_cli_lc_prefill_probe_requires_diagnose(self):
        with patch.object(runner, "run", side_effect=AssertionError("must not run")), \
                patch.object(sys, "argv", ["runner", "--co", "CO-0649", "--revision", SURFACE_REVISION,
                                           "--route", runner.SURFACE_ENGINE, "--probe-lc-prefill"]), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runner.main()

    def test_disabled_confirm_logs_the_component_error_slots_and_empty_fields(self):
        # Live shape 2026-09-30: one hook state object with "<field>Error" keys.
        found = {"states": [{"where": "17:hook8", "fields": 30, "errorSlots": 12,
                             "set": ["leakedCredentialsDomainsError: Invalid domain " + SURFACE_ALT],
                             "empty": ["leakedCredentialsDomains", "accountNetworks"]}]}
        with self._confirm_enabled_when(lambda page: False), \
                patch.object(_RPPage, "evaluate", lambda self, script, *a: found if "Error$" in script else None,
                             create=True):
            result, _page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_confirm_not_enabled")
        self.assertNotIn(SURFACE_ALT, runner.RUN_LOG_PATH.read_text(encoding="utf-8"))
        events = [(e["outcome"], e.get("detail", "")) for e in self._events() if e["step"] == "form_field_errors"]
        self.assertIn(("error_set", "17:hook8 leakedCredentialsDomainsError: Invalid domain <domain>"), events)
        self.assertIn(("empty_field", "17:hook8 leakedCredentialsDomains"), events)

    def test_diagnose_logs_the_react_form_errors_without_values(self):
        state = {"errors": ["leakedCredentialsScanningInterval: Required", "licenseDomains: Invalid " + SURFACE_ALT],
                 "flags": ["7:Formik.props.isValid=false"], "props": ["2:Button.disabled=true"],
                 "sources": ["7:Formik.props.errors keys=2"]}
        with self._confirm_enabled_when(lambda page: False), \
                patch.object(_RPPage, "evaluate", lambda self, script, *a: state if "__reactFiber" in script else None,
                             create=True):
            result, _page = self._run(self._scenario(), diagnose=True)
        self.assertEqual(result, "diagnose_confirm_blocker_unknown")
        raw = runner.RUN_LOG_PATH.read_text(encoding="utf-8")
        self.assertNotIn(SURFACE_ALT, raw)
        details = [e.get("detail", "") for e in self._events() if e["step"] == "react_form_state"]
        self.assertIn("leakedCredentialsScanningInterval: Required", details)
        self.assertIn("7:Formik.props.isValid=false", details)

    def test_form_validation_report_is_logged_without_values(self):
        report = {"controls": ["Maximum scan Duration (hours) [invalid:Value must be <= 72;min=1;max=72;step=1]"],
                  "messages": ["Invalid domain " + SURFACE_ALT], "confirm": ["disabled=true aria-disabled=null title="]}
        with self._confirm_enabled_when(lambda page: False), \
                patch.object(_RPPage, "evaluate", lambda self, script, *a: report, create=True):
            result, _page = self._run(self._scenario(), dry_run=True)
        self.assertEqual(result, "dry_run_confirm_not_enabled")
        raw = runner.RUN_LOG_PATH.read_text(encoding="utf-8")
        self.assertNotIn(SURFACE_ALT, raw)
        details = [e.get("detail", "") for e in self._events() if e["step"] == "form_validation"]
        self.assertTrue(any("max=72" in d for d in details))
        self.assertTrue(any("<domain>" in d or "<value>" in d for d in details))

    def test_happy_path_creates_and_reads_back_a_scanning_tenant(self):
        result, page = self._run(self._scenario())
        self.assertEqual(result, "readback_verified")
        self.assertTrue(page.confirmed)
        plan = runner.build_surface_only_fill(_surface_source(), RUN_DAY)
        for label, expected in plan["texts"].items():
            self.assertEqual(page.filled.get(label), expected, label)
        self.assertEqual(page.filled["Maximum scan Duration (hours)"], "90")
        self.assertEqual(page.selected, plan["selects"])
        self.assertNotIn("Leaked Credentials scanning interval", page.selected)
        for key, target in plan["checkboxes"].items():
            self.assertIs(page.checkbox_states[key], target, key)
        for label in ("Networks (Comma Separated Values)", "Phone number", "Job title", "Operator Account"):
            self.assertEqual(page.filled.get(label, ""), "", label)
        self.assertEqual(page.filled["Start date"], "2026-09-29")
        raw = json.loads(self.readbacks.read_text(encoding="utf-8"))
        self.assertEqual(raw["CO-0801"]["leonardo_state"], "Account Scanning")
        self.assertEqual(raw["CO-0801"]["surface_account_id"], SURFACE_ID)
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["CO-0801"]["result"], "readback_verified")
        log = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]
        self.assertEqual(log["route"], runner.SURFACE_ENGINE)
        steps = [(e["step"], e["outcome"]) for e in log["events"]]
        self.assertIn(("max_scan_duration", "ok"), steps)
        self.assertIn(("verify_operator_account", "ok"), steps)
        self.assertIn(("verify_blank", "ok"), steps)
        lookups = [e.get("field") for e in log["events"] if e["step"] == "duplicate_check"]
        self.assertEqual(lookups, ["tenant_name", "primary_domain"])
        # Scan now is removed by the Monthly schedule and verified absent.
        self.assertIn(("absent_toggle", "ok"), steps)
        self.assertNotIn(("fill_toggle", "ok"), [(e["step"], e["outcome"]) for e in log["events"]
                                                 if e.get("field") == "scan_now"])

    def test_scan_now_still_present_with_a_schedule_fails_closed(self):
        # If the form ever kept Scan now visible under a schedule, the plan's
        # assumption is wrong: stop before Confirm instead of guessing.
        original = _RPCheckboxControl.count
        with patch.object(_RPCheckboxControl, "count",
                          lambda control: 1 if control.key == "scan_now" else original(control)):
            result, page = self._run(self._scenario())
        self.assertEqual(result, "fill_form_schema_unavailable")
        self.assertFalse(page.confirmed)

    def test_run_log_redacts_every_surface_source_value(self):
        source = _surface_source()
        helper = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(helper.doCleanups)
        page = helper._install_fake_playwright(self._scenario(), {})
        log = runner.RunLog("CO-0801", "create", route=runner.SURFACE_ENGINE)
        log.add_redactions(*runner.SURFACE_ROUTE.redactions(source))
        email = runner.surface_primary_user_email(source)
        log.event("x", "y", detail=" ".join((SURFACE_ACCOUNT, SURFACE_TENANT, SURFACE_MAIN, SURFACE_ALT,
                                             SURFACE_SUB, email)))
        detail = log.events[-1]["detail"].casefold()
        for value in (SURFACE_ACCOUNT, SURFACE_MAIN, SURFACE_ALT, SURFACE_SUB, email):
            self.assertNotIn(value.casefold(), detail)
        self.assertIsNotNone(page)

    def test_readback_without_scan_is_no_scan_started(self):
        result, _page = self._run(self._scenario(details={"Surface Account ID": SURFACE_ID, "Account UUID": UUID,
                                                          "Account Scanning": None}))
        self.assertEqual(result, "readback_verified")
        self.assertEqual(json.loads(self.readbacks.read_text(encoding="utf-8"))["CO-0801"]["leonardo_state"],
                         "No scan started")

    def test_missing_max_duration_control_fails_closed_before_confirm(self):
        result, page = self._run(self._scenario(max_duration_control=False))
        self.assertEqual(result, "max_scan_duration_schema_unavailable")
        self.assertFalse(page.confirmed)
        events = json.loads(runner.RUN_LOG_PATH.read_text(encoding="utf-8"))["runs"][-1]["events"]
        self.assertIn(("max_scan_duration", "not_found", "Maximum scan Duration (hours)"),
                      [(e["step"], e["outcome"], e.get("field")) for e in events])

    def test_max_duration_resolved_by_its_form_control_label_and_defaults_overwritten(self):
        result, page = self._run(self._scenario(max_duration_by_form_control=True))
        self.assertEqual(result, "readback_verified")
        # Live defaults 24 h and 50000 subdomains are overwritten and re-read.
        self.assertEqual(page.filled["Maximum scan Duration (hours)"], "90")
        self.assertEqual(page.filled["Number of subdomains"], "500")
        self.assertIsNone(runner._locate_form_field(page, "Maximum scan Duration (hours)", ""))

    def test_leaked_credentials_dependents_stay_disabled_and_untouched(self):
        result, page = self._run(self._scenario())
        self.assertEqual(result, "readback_verified")
        self.assertIs(page.checkbox_states["leakedCredentialsAllowed"], False)
        self.assertNotIn("Leaked Credentials scanning interval", page.selected)
        self.assertNotIn("Leaked Credentials scanned domains (Comma Separated Values)", page.filled)
        self.assertFalse(_RPControl(page, "Leaked Credentials scanning interval", "select").is_enabled())

    def test_max_duration_that_does_not_keep_90_fails_closed(self):
        original_fill = _RPControl.fill

        def sticky(control, value, **kwargs):
            if control.label == "Maximum scan Duration (hours)":
                return  # keeps the live default of 24
            original_fill(control, value, **kwargs)

        with patch.object(_RPControl, "fill", sticky):
            result, page = self._run(self._scenario())
        self.assertEqual(result, "fill_value_mismatch")
        self.assertFalse(page.confirmed)

    def test_operator_account_must_be_verified_empty(self):
        for extra, code in (({"operator_selected": 1}, "fill_value_mismatch"),
                            ({"prefilled": {"Operator Account": "someone"}}, "fill_value_mismatch"),
                            ({"operator_inputs": 0}, "fill_form_schema_unavailable")):
            with self.subTest(extra=extra):
                result, page = self._run(self._scenario(**extra))
                self.assertEqual(result, code)
                self.assertFalse(page.confirmed)

    def test_non_empty_blank_control_fails_closed(self):
        result, page = self._run(self._scenario(prefilled={"Networks (Comma Separated Values)": "10.0.0.0/8"}))
        self.assertEqual(result, "fill_value_mismatch")
        self.assertFalse(page.confirmed)

    def test_duplicate_by_primary_domain_blocks(self):
        result, page = self._run(self._scenario(tenants=[_RPRow("Other Co", SURFACE_MAIN)]))
        self.assertEqual(result, "duplicate_found")
        self.assertFalse(page.confirmed)
        self.assertNotIn("Company name", page.filled)

    def test_source_error_stops_before_the_browser(self):
        helper = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(helper.doCleanups)
        tracker: dict = {}
        page = helper._install_fake_playwright(self._scenario(), tracker)
        for code in ("surface_route_mismatch", "surface_baseline_ambiguous", "surface_networks_not_supported"):
            with self.subTest(code=code), \
                    patch.object(runner, "surface_fill_source", side_effect=runner.SurfaceSourceError(code)), \
                    patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                    patch.object(runner, "_attach_attended_browser", helper._attach(page, tracker)):
                self.assertEqual(runner.run("CO-0801", SURFACE_REVISION, route=runner.SURFACE_ENGINE), code)
        self.assertFalse(tracker.get("launched", False))

    def test_unknown_route_fails_closed(self):
        with patch.object(runner, "RUNNER_STATE_PATH", self._temp_path("state.json")), \
                patch.object(runner, "surface_fill_source", side_effect=AssertionError("no read")), \
                patch.object(runner, "ce_fill_source", side_effect=AssertionError("no read")):
            self.assertEqual(runner.run("CO-0801", SURFACE_REVISION, route="case_3_combined_baseline"),
                             "route_unsupported")
            self.assertEqual(runner.run_readback("CO-0801", route="nope"), "route_unsupported")
            self.assertEqual(runner.run_duplicate_check("CO-0801", route="nope"), "route_unsupported")

    def test_surface_readback_only_accepts_a_scanning_tenant(self):
        helper = RunEndToEndTests("test_readback_verified_happy_path")
        self.addCleanup(helper.doCleanups)
        tracker: dict = {}
        page = helper._install_fake_playwright(self._scenario(tenants=[_RPRow(SURFACE_TENANT, SURFACE_MAIN)]), tracker)
        readbacks = self._temp_path("readbacks.json")
        state = self._temp_path("state.json")
        with patch.object(runner, "surface_fill_source", return_value=_surface_source()), \
                patch.object(runner, "READBACK_PATH", readbacks), \
                patch.object(runner, "RUNNER_STATE_PATH", state), \
                patch.object(runner, "_readback_details_optional_state", return_value=None), \
                patch.object(runner, "_attach_attended_browser", helper._attach(page, tracker)):
            self.assertEqual(runner.run_readback("CO-0801", route=runner.SURFACE_ENGINE), "readback_only_verified")
        self.assertEqual(json.loads(readbacks.read_text(encoding="utf-8"))["CO-0801"]["leonardo_state"],
                         "Account Scanning")
        self.assertFalse(state.exists())


class RunnerStateRouteTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp(prefix="ce_state_route_"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        patcher = patch.object(runner, "RUNNER_STATE_PATH", directory / "state.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_surface_start_records_route_and_scope_review(self):
        record_runner_start("CO-0801", "rev1", "2026-09-29T10:00:00", route=runner.SURFACE_ENGINE,
                            scope_reviewed_on="2026-09-29T10:00:00")
        record_runner_result("CO-0801", "rev1", "readback_verified", "2026-09-29T10:02:00")
        self.assertEqual(load_runner_state()["CO-0801"], {
            "source_revision": "rev1", "route": runner.SURFACE_ENGINE, "started_on": "2026-09-29T10:00:00",
            "scope_reviewed_on": "2026-09-29T10:00:00", "result": "readback_verified",
            "completed_on": "2026-09-29T10:02:00"})

    def test_ce_start_record_is_unchanged(self):
        record_runner_start("CO-0702", "rev1", "2026-09-29T10:00:00")
        raw = json.loads(runner.RUNNER_STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw, {"CO-0702": {"source_revision": "rev1", "started_on": "2026-09-29T10:00:00"}})

    def test_invalid_route_or_review_time_is_rejected(self):
        with self.assertRaises(ValueError):
            record_runner_start("CO-0801", "rev1", "2026-09-29T10:00:00", route="case_9")
        with self.assertRaises(ValueError):
            record_runner_start("CO-0801", "rev1", "2026-09-29T10:00:00", scope_reviewed_on="yesterday")
        for value in ({"source_revision": "r", "route": "case_9"},
                      {"source_revision": "r", "scope_reviewed_on": "not-a-date"},
                      {"source_revision": "r", "scope_reviewed_on": 5}):
            with self.subTest(value=value):
                runner.RUNNER_STATE_PATH.write_text(json.dumps({"CO-0801": value}), encoding="utf-8")
                with self.assertRaises(RunnerStateUnavailable):
                    load_runner_state()


class RouteCliTests(unittest.TestCase):
    def _main(self, *argv):
        captured = {}

        def fake_run(reference, revision, **kwargs):
            captured.update(reference=reference, revision=revision, **kwargs)
            return "readback_verified"

        with patch.object(sys, "argv", ["runner", *argv]), patch.object(runner, "run", fake_run), \
                contextlib.redirect_stdout(open(os.devnull, "w")) as sink:
            self.addCleanup(sink.close)
            self.assertEqual(runner.main(), 0)
        return captured

    def test_default_route_is_ce(self):
        self.assertEqual(self._main("--co", "CO-0702", "--revision", "r")["route"], runner.CE_ENGINE)

    def test_surface_route_is_passed_through(self):
        captured = self._main("--co", "CO-0801", "--revision", "r", "--route", runner.SURFACE_ENGINE)
        self.assertEqual(captured["route"], runner.SURFACE_ENGINE)

    def test_unknown_route_is_rejected_by_the_cli(self):
        with patch.object(sys, "argv", ["runner", "--co", "CO-0801", "--revision", "r", "--route", "x"]), \
                contextlib.redirect_stderr(open(os.devnull, "w")) as sink:
            self.addCleanup(sink.close)
            with self.assertRaises(SystemExit):
                runner.main()


if __name__ == "__main__":
    unittest.main()
