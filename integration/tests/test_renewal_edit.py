"""Renewal EDIT mode (Cases 4-6, owner decisions 2026-10-06): source, plan, domain gate, edit flow, CLI.

No network, no browser: every page is a recording fake (the SpyCloud fake), every file is a temporary one.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from integration.onboarding import leonardo_inventory as inventory
import tools.attended_ce_only_playwright as runner
from integration.tests.test_spycloud_off import FakePage, ID, ORIGIN, UUID, _Response, edit_url

TODAY = date(2026, 10, 6)
TENANT = "Acme"
DOMAIN = "acme.com"
CE_DOMAIN = "acme-ce.com"
SCE = "Surface & Credential Exposure"
RENEW = "Renewal of Existing Product"


def ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp() * 1000)


def put(row: dict, path: str, value) -> dict:
    node = row
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
    return row


def tenant_row(**overrides) -> dict:
    """A Dev/mirror tenant row before the renewal: old term, LC off, phishing on, interval none."""
    row: dict = {
        "id": ID, "accountUuid": UUID, "accountName": TENANT, "accountDomain": DOMAIN, "accountType": "Customer",
        "accountCountryCode": "US", "enabled": True, "isDeleted": False,
        "scanningInterval": "NO_SCHEDULE", "leakedCredentialsScanningInterval": "NO_SCHEDULE",
        "alternateDomains": ["acme.net"], "subDomains": ["app.acme.com"], "leakedCredentialsScannedDomains": [],
        "userEmailDomains": ["pentera.io"], "additionalNetworks": [], "operatorAccounts": [],
        "campaignsTimeoutInHours": 90,
        "primaryUser": {"email": "a+b@pentera.io", "firstName": "M", "lastName": "S", "isMfaRequired": True},
        "leakedCredentialsSettings": {"spyCloudSettings": {"enabled": False}},
    }
    put(row, "accountLicense.enabled", True)
    put(row, "accountLicense.startDate", ms(date(2025, 10, 1)))
    put(row, "accountLicense.expirationDate", ms(date(2026, 10, 15)))
    put(row, "accountLicense.licenseType", "PREPAID_ANNUAL_SUBSCRIPTION")
    put(row, "accountLicense.assetsNumber", 5000)
    put(row, "accountLicense.domainsNumber", 5)
    put(row, "accountLicense.subDomainsNumber", 5)
    for key, path in runner.VALIDATION_TOGGLE_PATHS.items():
        put(row, path, key in ("notificationsAllowed", "multipleUsersAllowed", "apiAccessAllowed", "phishingEnabled",
                               "provisioningEnabled", "subDomainsReconEnabled"))
    for path, value in overrides.items():
        put(row, path.replace("__", "."), value)
    return row


def source(**overrides) -> runner.RenewalSource:
    term = {"start": "2026-10-16", "end": "2029-10-15", "annual_expiration": "2027-10-15",
            "scanning_interval": "Weekly", "subdomains": 10, "domains": 10, "assets": 10000, "tier": "prime"}
    values = dict(reference="CO-0900", source_revision="rev", engine="case_6_renew_both", label="Case 6",
                  account_name=TENANT, tenant_name=TENANT, ce_tenant_name=TENANT + " - CE Only", main_domain=DOMAIN,
                  alternate_domains=("acme.net",), subdomains=("app.acme.com",), ce_email_domain=CE_DOMAIN,
                  surface_term=term, ce_term={"start": "2026-10-16"}, production_ids_present=True)
    values.update(overrides)
    return runner.RenewalSource(**values)


def result_of(*rows, total=None):
    return runner.TenantSearchResult(list(rows), len(rows) if total is None else total)


CLEAR = {"result": "renewal_gate_clear", "blocks": False, "matches": []}


def after_row(before: dict, **overrides) -> dict:
    """The row after a correct save: every planned field applied, everything else untouched."""
    row = copy.deepcopy(before)
    put(row, "accountLicense.expirationDate", ms(date(2029, 10, 15)))  # Q1: the term end, not start + 1 year
    put(row, "accountLicense.licenseType", "PREPAID_ANNUAL_SUBSCRIPTION")
    row["scanningInterval"] = "WEEKLY"
    put(row, "accountLicense.assetsNumber", 10000)
    put(row, "accountLicense.domainsNumber", 10)
    put(row, "accountLicense.subDomainsNumber", 10)
    put(row, runner.VALIDATION_TOGGLE_PATHS["leakedCredentialsAllowed"], True)
    put(row, runner.VALIDATION_TOGGLE_PATHS["phishingEnabled"], False)
    row["leakedCredentialsScanningInterval"] = "WEEKLY"
    row["leakedCredentialsScannedDomains"] = [CE_DOMAIN]
    for path, value in overrides.items():
        put(row, path.replace("__", "."), value)
    return row


class SourceTests(unittest.TestCase):
    def read(self, product=SCE, onboarding_type=RENEW, *, approval="Approved", co_extra=None, rows=None):
        co = {"Name": "CO-0900", "LastModifiedDate": "2026-10-05T10:00:00.000+0000", "Account__c": "0015g00000AbCdE",
              "Account_Name__c": TENANT, "Account_Country__c": "United States", "Main_Domain__c": DOMAIN,
              "Alternate_Domains__c": "acme.net, app.acme.com", "Email_Domains__c": CE_DOMAIN,
              "Onboarding_Product__c": product, "Onboarding_Type__c": onboarding_type,
              "Surface_Account_ID__c": "prod-surface-id", "Account_UUID__c": "prod-uuid",
              "Onboarding_Approval_Status__c": approval, **(co_extra or {})}
        dealhub = rows if rows is not None else [
            _row("Pentera Surface Software Enterprise - Up to 1000 Sub-Domains & 10 Domains", "Active", "2025-10-16", "2026-10-15"),
            _row("Pentera Surface Prime - 1000 Subdomains", "Pending", "2026-10-16", "2029-10-15"),
            _row("Pentera Core Plus Commercial - 500 End Points", "Pending", "2026-10-16", "2029-10-15"),
        ]
        with patch.object(runner, "_sf_records", side_effect=[[co], dealhub]), \
                patch.object(runner, "_run_day", return_value=TODAY):
            return runner.renewal_fill_source("CO-0900")

    def assertRefused(self, code, **kw):
        with self.assertRaises(RuntimeError) as caught:
            self.read(**kw)
        self.assertEqual(str(caught.exception), code)

    def test_case6_source_reads_the_new_term_and_ignores_production_ids(self):
        read = self.read()
        self.assertEqual(read.engine, "case_6_renew_both")
        self.assertTrue(read.production_ids_present)  # expected on a renewal; it is not a gate
        self.assertEqual(read.licensed_subdomains, 1000)
        self.assertEqual(runner.renewal_expiration(read), date(2029, 10, 15))  # Q1 (revised): the full term end

    def test_expiration_is_the_term_end_exactly(self):
        short = source(surface_term={**source().surface_term, "start": "2026-10-16", "end": "2027-03-01"})
        self.assertEqual(runner.renewal_expiration(short), date(2027, 3, 1))
        multi = source(surface_term={**source().surface_term, "start": "2026-09-30", "end": "2029-09-29"})
        self.assertEqual(runner.renewal_expiration(multi), date(2029, 9, 29))  # not start + 1 year - 1 day

    def test_case_4_and_5_types_are_accepted(self):
        for onboarding_type, engine in (("Renewal of Surface + New Credential Exposure Module", "case_4_renew_surface_new_ce"),
                                        ("Renewal of Credential Exposure Module + New Surface Product", "case_5_renew_ce_new_surface")):
            self.assertEqual(self.read(onboarding_type=onboarding_type).engine, engine)

    def test_single_product_and_new_onboarding_routes_are_refused(self):
        self.assertRefused("renewal_route_not_supported", product="Surface")
        self.assertRefused("renewal_route_not_supported", product="Credential Exposure")
        self.assertRefused("renewal_route_mismatch", onboarding_type="New Product Onboarding")

    def test_a_co_that_is_not_approved_is_refused(self):
        self.assertRefused("renewal_not_approved", approval="Pending")
        self.assertRefused("renewal_not_approved", approval=None)

    def test_a_plan_blocker_and_bad_domains_fail_closed(self):
        self.assertRefused("renewal_blocked_no_core_plus_term", rows=[
            _row("Pentera Surface Prime - 1000 Subdomains", "Pending", "2026-10-16", "2029-10-15")])
        self.assertRefused("renewal_ce_email_domain_invalid", co_extra={"Email_Domains__c": "a.com, b.com"})
        self.assertRefused("surface_domains_exceed_license", rows=[
            _row("Pentera Surface Go - 1 Subdomains", "Pending", "2026-10-16", "2029-10-15"),
            _row("Pentera Core Plus Commercial - 500 End Points", "Pending", "2026-10-16", "2029-10-15")])

    def test_the_source_never_carries_a_start_date(self):
        self.assertNotIn("start", [field.name for field in runner.dataclasses.fields(runner.RenewalSource)
                                   if field.name.endswith("_start")])
        contract = runner.RENEWAL_ROUTES["case_6_renew_both"]
        self.assertEqual(contract.license_dates(source())[0], None)


def _row(product, status, start, end):
    return {"Product_Full_Name__c": product, "DealHub_Status__c": status,
            "DealHub_Subscription_Start_Date__c": start, "DealHub_Subscription_End_Date__c": end}


class CeSubscriptionStatusTests(unittest.TestCase):
    """Review item 20: select_ce_subscription respects DealHub_Status__c."""

    def test_an_expired_term_next_to_an_active_core_plus_term_is_not_ambiguous(self):
        rows = [_row("Pentera Core Plus Commercial - 500 End Points", "Expired", "2023-09-19", "2026-09-18"),
                _row("Pentera Core Plus Commercial - 500 End Points", "Active", "2026-09-30", "2029-09-29")]
        self.assertEqual(runner.select_ce_subscription(rows), (date(2026, 9, 30), date(2029, 9, 29)))

    def test_only_inactive_rows_are_unavailable_and_rows_without_a_status_are_unchanged(self):
        with self.assertRaises(RuntimeError) as caught:
            runner.select_ce_subscription([_row("Pentera Core Plus Commercial - 5", "Cancelled", "2026-01-01", "2027-01-01")])
        self.assertEqual(str(caught.exception), "ce_subscription_unavailable")
        plain = {"Product_Full_Name__c": "Pentera Core Plus Commercial - 5",
                 "DealHub_Subscription_Start_Date__c": "2026-01-01", "DealHub_Subscription_End_Date__c": "2027-01-01"}
        self.assertEqual(runner.select_ce_subscription([plain]), (date(2026, 1, 1), date(2027, 1, 1)))

    def test_two_live_terms_stay_ambiguous_unless_the_latest_is_preferred(self):
        rows = [_row("Pentera Core Plus Commercial - 5", "Active", "2025-10-01", "2026-10-15"),
                _row("Pentera Core Plus Commercial - 5", "Pending", "2026-10-16", "2029-10-15")]
        with self.assertRaises(RuntimeError) as caught:
            runner.select_ce_subscription(rows)
        self.assertEqual(str(caught.exception), "ce_subscription_ambiguous")
        self.assertEqual(runner.select_ce_subscription(rows, prefer_latest=True), (date(2026, 10, 16), date(2029, 10, 15)))


class PlanTests(unittest.TestCase):
    def plan(self, row=None, src=None):
        row = row or tenant_row()
        specs, added, errors = runner.build_renewal_specs(src or source(), row)
        return specs, added, errors, row

    def test_the_start_date_is_never_planned_or_preserved_as_a_change(self):
        specs, _added, _errors, _row_ = self.plan()
        self.assertNotIn("license_start", {spec.key for spec in specs})
        self.assertNotIn("accountLicense.startDate", {spec.path for spec in specs})
        _snapshot = runner.renewal_preserve_snapshot(tenant_row(), specs)
        self.assertIn("accountLicense.startDate", _snapshot)  # it is verified unchanged instead

    def test_diff_lists_only_fields_that_differ_with_checkboxes_first(self):
        specs, added, errors, row = self.plan()
        self.assertEqual(errors, [])
        changes, error = runner.plan_renewal_changes(row, specs, TODAY)
        self.assertIsNone(error)
        keys = [change["spec"].key for change in changes]
        self.assertEqual(keys[:2], ["leakedCredentialsAllowed", "phishingEnabled"])
        self.assertEqual(set(keys), {
            "leakedCredentialsAllowed", "phishingEnabled", "license_end", "Scanning interval", "Number of assets",
            "Number of domains", "Number of subdomains", "Leaked Credentials scanning interval",
            "Leaked Credentials scanned domains (Comma Separated Values)"})  # Type already matches; lists add nothing
        self.assertEqual(added, {CE_DOMAIN})

    def test_domain_lists_are_add_only_and_never_remove(self):
        row = tenant_row(alternateDomains=["old.example", "acme.net"])
        specs, added, _errors, _ = self.plan(row)
        self.assertEqual(added, {CE_DOMAIN})
        src = source(alternate_domains=("acme.net", "new-root.com"))
        specs, added, _errors, _ = self.plan(row, src)
        alternate = next(spec for spec in specs if spec.path == "alternateDomains")
        self.assertEqual(alternate.target, ("old.example", "acme.net", "new-root.com"))
        self.assertEqual(added, {"new-root.com", CE_DOMAIN})

    def test_a_domain_already_on_the_tenant_is_not_new_for_the_gate(self):
        row = tenant_row(leakedCredentialsScannedDomains=[], alternateDomains=["acme.net", CE_DOMAIN])
        _specs, added, _errors, _ = self.plan(row)
        self.assertEqual(added, set())

    def test_unexpected_row_shapes_fail_closed(self):
        _s, _a, errors, _ = self.plan(tenant_row(leakedCredentialsScannedDomains=None))
        self.assertEqual(errors, ["renewal_row_schema_unexpected"])
        row = tenant_row()
        put(row, runner.VALIDATION_TOGGLE_PATHS["phishingEnabled"], None)
        _s, _a, errors, _ = self.plan(row)
        self.assertEqual(errors, ["renewal_row_schema_unexpected"])

    def test_expiration_guards(self):
        specs, _a, _e, row = self.plan()
        self.assertEqual(runner.plan_renewal_changes(row, specs, date(2029, 10, 16))[1], "renewal_expiration_in_past")
        later = tenant_row(accountLicense__expirationDate=ms(date(2030, 1, 1)))
        self.assertEqual(runner.plan_renewal_changes(later, specs, TODAY)[1], "renewal_expiration_would_shorten")

    def test_nothing_to_change_gives_an_empty_diff(self):
        row = after_row(tenant_row())
        specs, _added, _errors, _ = self.plan(row)
        self.assertEqual(runner.plan_renewal_changes(row, specs, TODAY), ([], None))

    def test_an_expired_old_licence_still_keeps_its_start_date_out_of_the_plan(self):
        row = tenant_row(accountLicense__expirationDate=ms(date(2026, 1, 1)), accountLicense__enabled=False)
        specs, _a, _e, _ = self.plan(row)
        changes, error = runner.plan_renewal_changes(row, specs, TODAY)
        self.assertIsNone(error)
        self.assertNotIn("license_start", {change["spec"].key for change in changes})

    def test_start_protection_is_enforced_even_if_a_spec_asked_for_it(self):
        rogue = runner.FieldSpec("date", "license_start", "accountLicense.startDate", date(2026, 1, 1))
        self.assertEqual(runner.plan_renewal_changes(tenant_row(), [rogue], TODAY)[1], "renewal_start_date_protected")
        self.assertEqual(runner._apply_spec(object(), rogue), "renewal_start_date_protected")

    def test_readback_checks_changed_and_preserved_fields(self):
        before = tenant_row()
        specs, _added, _errors, _ = self.plan(before)
        changes, _ = runner.plan_renewal_changes(before, specs, TODAY)
        snapshot = runner.renewal_preserve_snapshot(before, specs)
        self.assertEqual(runner.verify_renewal_row(after_row(before), changes, snapshot), ([], []))
        changed_bad, _ = runner.verify_renewal_row(before, changes, snapshot)
        self.assertIn("license_end", changed_bad)
        moved = after_row(before, accountLicense__startDate=ms(date(2026, 10, 16)))
        self.assertEqual(runner.verify_renewal_row(moved, changes, snapshot)[1], ["accountLicense.startDate"])
        spy = after_row(before, leakedCredentialsSettings__spyCloudSettings__enabled=True)
        self.assertEqual(runner.verify_renewal_row(spy, changes, snapshot)[1],
                         ["leakedCredentialsSettings.spyCloudSettings.enabled"])

    def test_profile_drift_is_reported_only(self):
        self.assertIn("aiEnabled", runner.renewal_profile_drift(tenant_row(aiEnabled=True)))
        specs, _a, _e, _ = self.plan()
        self.assertNotIn("aiEnabled", {spec.key for spec in specs})


def prod_payload(tenants):
    return {"tenants": tenants, "captured_at": "2026-10-06T08:00:00+00:00", "environment_label": "Prod (Cloned)"}


def prod_tenant(tenant_id, name, domain, alternates=(), deleted=False, enabled=True,
                license_type="Prepaid annual subscription"):
    """Live and paid by default (the renewal's real target); pass enabled / license_type for the other cases."""
    return {"id": tenant_id, "account_name": name, "account_domain": domain, "alternate_domains": sorted(alternates),
            "is_deleted": deleted, "enabled": enabled, "license": {"type": license_type}, "created": "2024-01-01"}


class DomainGateTests(unittest.TestCase):
    NOW = datetime(2026, 10, 6, 9, tzinfo=timezone.utc)

    def gate(self, tenants, new=("new-domain.com",), names=(TENANT,), domain=DOMAIN, payload=None):
        with patch.object(inventory, "load_latest", return_value=payload or prod_payload(tenants)):
            return inventory.renewal_domain_gate(Path("."), names, domain, new, now=self.NOW)

    def test_no_new_domain_needs_no_clone(self):
        with patch.object(inventory, "load_latest", side_effect=AssertionError("not read")):
            gate = inventory.renewal_domain_gate(Path("."), (TENANT,), DOMAIN, [], now=self.NOW)
        self.assertEqual((gate["result"], gate["blocks"]), ("renewal_gate_not_needed", False))

    def test_a_new_domain_on_no_other_tenant_is_clear_and_the_own_tenant_is_excluded(self):
        own = prod_tenant("p1", TENANT, DOMAIN, ["new-domain.com"])  # already on the target itself: not a duplicate
        other = prod_tenant("p2", "Other", "other.com")
        gate = self.gate([own, other])
        self.assertEqual((gate["result"], gate["blocks"]), ("renewal_gate_clear", False))

    def test_a_new_domain_on_another_tenant_blocks_primary_alternate_and_deleted(self):
        own = prod_tenant("p1", TENANT, DOMAIN)
        for other in (prod_tenant("p2", "Other", "new-domain.com"),
                      prod_tenant("p3", "Other2", "other.com", ["New-Domain.com"]),
                      prod_tenant("p4", "Gone", "gone.com", ["new-domain.com"], deleted=True)):
            gate = self.gate([own, other])
            self.assertEqual((gate["result"], gate["blocks"]), ("renewal_new_domain_in_production", True))
            self.assertEqual([m["id"] for m in gate["matches"]], [other["id"]])

    def test_the_target_must_match_exactly_once(self):
        other = prod_tenant("p2", "Other", "other.com", [])
        self.assertEqual(self.gate([other])["result"], "renewal_target_not_in_production_clone")
        twin = [prod_tenant("p1", TENANT, DOMAIN), prod_tenant("p9", TENANT, DOMAIN)]
        self.assertEqual(self.gate(twin + [other])["result"], "renewal_target_ambiguous_in_production_clone")
        # A disabled same-name/same-domain twin is not a second target; two live paid twins still are.
        disabled = prod_tenant("p8", TENANT, DOMAIN, enabled=False)
        self.assertEqual(self.gate([prod_tenant("p1", TENANT, DOMAIN), disabled, other])["result"], "renewal_gate_clear")
        self.assertEqual(self.gate([disabled, other])["result"], "renewal_target_not_in_production_clone")
        # Trial / Evaluation tenants of the same name and domain are never the target.
        for kind in ("Trial", "Evaluation"):
            self.assertEqual(self.gate([prod_tenant("p7", TENANT, DOMAIN, license_type=kind), other])["result"],
                             "renewal_target_not_in_production_clone")
        # Same name on another domain is a different tenant, not the target.
        self.assertEqual(self.gate([prod_tenant("p1", TENANT, "different.com"), other])["result"],
                         "renewal_target_not_in_production_clone")
        # The CE-only name also identifies the target.
        ce = prod_tenant("p5", TENANT + " - CE Only", DOMAIN)
        self.assertEqual(self.gate([ce, other], names=(TENANT, TENANT + " - CE Only"))["result"], "renewal_gate_clear")

    def test_an_unusable_clone_stops(self):
        with patch.object(inventory, "load_latest", side_effect=inventory.InventoryError("inventory_stale")):
            gate = inventory.renewal_domain_gate(Path("."), (TENANT,), DOMAIN, ["new-domain.com"], now=self.NOW)
        self.assertEqual((gate["result"], gate["blocks"], gate["reason"]), ("production_clone_unavailable", True, "inventory_stale"))
        incomplete = [{"id": "p1", "account_name": TENANT, "account_domain": DOMAIN, "alternate_domains": None}]
        gate = self.gate(incomplete)
        self.assertEqual((gate["result"], gate["blocks"]), ("production_clone_incomplete", True))


class FormPage(FakePage):
    """The SpyCloud fake plus the Confirm button behaviour the renewal edit needs."""


def ok_gate(domains):
    return dict(CLEAR)


class RenewTenantTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.applied: list[str] = []
        self.before = tenant_row()
        self.form_start = date(2025, 10, 1)

        def apply(page, spec):
            self.applied.append(spec.key)
            return None

        self.stack.enter_context(patch.object(runner, "_apply_spec", side_effect=apply))
        self.stack.enter_context(patch.object(runner, "_license_date_value", side_effect=lambda page, key: self.form_start))
        self.stack.enter_context(patch.object(runner, "_ensure_confirm_enabled", return_value=True))
        self.stack.enter_context(patch.object(runner, "_run_day", return_value=TODAY))
        self.form_current = self.stack.enter_context(patch.object(runner, "_form_current", side_effect=self.read_form))

    def read_form(self, page, spec):
        """The Edit form shows what the row says."""
        value = runner._row_value(self.before, spec.path)
        if spec.kind == "date":
            return sorted(runner._epoch_dates(value))[0] and date.fromisoformat(sorted(runner._epoch_dates(value))[0])
        if spec.kind == "select":
            return {"NO_SCHEDULE": "None", "WEEKLY": "Weekly"}.get(value, value)
        return value

    def run_edit(self, *, confirm_write, results, page=None, gate=ok_gate, src=None, **kw):
        page = page or FakePage(checked=False, response=_Response(edit_url()))
        with patch.object(runner, "_search_tenants", side_effect=list(results)):
            outcome = runner.renew_tenant(page, src or source(), TENANT, kw.pop("expected_id", ID), expected_uuid=UUID,
                                          gate=gate, confirm_write=confirm_write, today=TODAY, **kw)
        return outcome, page

    def test_dry_run_plans_reports_and_cancels_without_writing(self):
        (result, report), page = self.run_edit(confirm_write=False, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_dry_run_planned")
        self.assertEqual(self.applied, [])
        self.assertNotIn("Confirm", page.clicked)
        self.assertIn("Cancel", page.clicked)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["start_date"], "never_changed")
        self.assertEqual(report["added_domains"], 1)
        fields = {change["field"]: change for change in report["changes"]}
        self.assertEqual(fields["license_end"]["target"], "2029-10-15")
        self.assertEqual(fields["license_end"]["current"], "2026-10-15")
        self.assertEqual(fields["Number of subdomains"]["target"], 10)
        self.assertNotIn(CE_DOMAIN, json.dumps(report))  # domain lists are counts only
        self.assertEqual([s for s in page.selectors if "data-am" in s], [runner.SPYCLOUD_EDIT_SELECTOR])
        for hazard in ("Grid_Access", "Scan_Now", "Stop_Scan", "Delete"):
            self.assertFalse(any(hazard in s for s in page.selectors))

    def test_dry_run_with_a_form_that_stays_open_is_reported(self):
        (result, _report), _page = self.run_edit(confirm_write=False, results=[result_of(self.before)],
                                                 page=FakePage(checked=False, cancel=0))
        self.assertEqual(result, "renewal_cancel_unavailable")

    def test_already_current_never_opens_the_edit_form(self):
        self.before = after_row(tenant_row())
        (result, report), page = self.run_edit(confirm_write=True, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_already_current")
        self.assertEqual(report["changes"], [])
        self.assertEqual([s for s in page.selectors if "data-am" in s], [])
        self.assertEqual(self.applied, [])

    def test_confirm_write_applies_confirms_once_and_verifies_changed_and_preserved(self):
        (result, report), page = self.run_edit(confirm_write=True,
                                               results=[result_of(self.before), result_of(after_row(self.before))])
        self.assertEqual(result, "renewal_edit_verified")
        self.assertEqual(page.clicked.count("Confirm"), 1)
        self.assertEqual(self.applied[:2], ["leakedCredentialsAllowed", "phishingEnabled"])
        self.assertNotIn("license_start", self.applied)
        self.assertEqual(report["readback"]["changed_bad"], [])
        self.assertEqual(report["readback"]["preserved_bad"], [])
        self.assertGreater(report["readback"]["preserved_ok"], 10)
        self.assertEqual(runner.renewal_write_label(result, True), "verified")

    def test_a_moved_start_date_after_the_save_is_a_preserved_field_mismatch(self):
        moved = after_row(self.before, accountLicense__startDate=ms(date(2026, 10, 16)))
        (result, report), _page = self.run_edit(confirm_write=True, results=[result_of(self.before), result_of(moved)])
        self.assertEqual(result, "renewal_readback_preserved_mismatch")
        self.assertEqual(report["readback"]["preserved_bad"], ["accountLicense.startDate"])
        self.assertEqual(runner.renewal_write_label(result, True), "attempted_unverified")

    def test_an_unchanged_field_after_the_save_is_a_changed_field_mismatch(self):
        stale = after_row(self.before, accountLicense__expirationDate=ms(date(2026, 10, 15)))
        (result, report), _page = self.run_edit(confirm_write=True, results=[result_of(self.before), result_of(stale)])
        self.assertEqual(result, "renewal_readback_changed_mismatch")
        self.assertEqual(report["readback"]["changed_bad"], ["license_end"])

    def test_the_domain_gate_stops_before_the_edit_form_opens(self):
        blocked = lambda domains: {"result": "renewal_new_domain_in_production", "blocks": True, "matches": [{"id": "p2"}]}
        for confirm in (False, True):
            (result, report), page = self.run_edit(confirm_write=confirm, results=[result_of(self.before)], gate=blocked)
            self.assertEqual(result, "renewal_new_domain_in_production")
            self.assertEqual(report["gate"], "renewal_new_domain_in_production")
            self.assertEqual([s for s in page.selectors if "data-am" in s], [])
            self.assertEqual(self.applied, [])

    def test_the_gate_receives_exactly_the_added_domains(self):
        seen = []
        self.run_edit(confirm_write=False, results=[result_of(self.before)], gate=lambda d: seen.append(d) or dict(CLEAR))
        self.assertEqual(seen, [[CE_DOMAIN]])

    def test_the_default_gate_reads_the_prod_clone_with_both_tenant_names(self):
        with patch.object(inventory, "renewal_domain_gate", return_value=dict(CLEAR)) as call, \
                patch.object(runner, "inventory_root", return_value=Path(".")):
            (result, _r), _page = self.run_edit(confirm_write=False, results=[result_of(self.before)], gate=None)
        self.assertEqual(result, "renewal_dry_run_planned")
        self.assertEqual(call.call_args.args[1], (TENANT, TENANT + " - CE Only"))
        self.assertEqual(call.call_args.args[2:4], (DOMAIN, [CE_DOMAIN]))

    def test_a_form_that_disagrees_with_the_row_stops_before_any_write(self):
        self.form_current.side_effect = lambda page, spec: 999 if spec.kind == "number" else self.read_form(page, spec)
        (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_form_mismatch")
        self.assertEqual(self.applied, [])
        self.assertNotIn("Confirm", page.clicked)
        self.form_current.side_effect = lambda page, spec: None
        (result, _r), _page = self.run_edit(confirm_write=False, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_form_unreadable")

    def test_a_start_date_control_that_changes_blocks_the_save(self):
        def apply(page, spec):
            self.form_start = date(2026, 10, 16)
        runner._apply_spec.side_effect = apply
        (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_start_date_changed")
        self.assertNotIn("Confirm", page.clicked)

    def test_a_field_that_cannot_be_set_stops_before_confirm(self):
        runner._apply_spec.side_effect = lambda page, spec: "renewal_field_unavailable"
        (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_field_unavailable")
        self.assertNotIn("Confirm", page.clicked)
        self.assertIn("Cancel", page.clicked)

    def test_confirm_must_be_unique_and_enabled_and_never_falls_back_to_another_start(self):
        (result, _r), _page = self.run_edit(confirm_write=True, results=[result_of(self.before)],
                                            page=FakePage(checked=False, confirm=2))
        self.assertEqual(result, "renewal_confirm_unavailable")
        runner._ensure_confirm_enabled.return_value = False
        (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before)])
        self.assertEqual(result, "renewal_confirm_disabled")
        self.assertNotIn("Confirm", page.clicked)
        self.assertEqual(runner._ensure_confirm_enabled.call_args.kwargs, {"allow_start_fallback": False})

    def test_save_outcomes(self):
        cases = (
            (_Response(edit_url(), 500), "renewal_save_failed"),
            (_Response(edit_url("z" * 24)), "renewal_save_id_mismatch"),
            (None, "renewal_save_no_signal"),
        )
        for response, expected in cases:
            page = FakePage(checked=False, response=response)
            (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before)], page=page)
            self.assertEqual(result, expected)
            self.assertEqual(page.clicked.count("Confirm"), 0 if response is None else 1)  # never re-clicked
            self.assertEqual(runner.renewal_write_label(result, True), "attempted_unverified")
        with self.assertRaises(runner.LeonardoSessionExpired):
            self.run_edit(confirm_write=True, results=[result_of(self.before)],
                          page=FakePage(checked=False, response=_Response(edit_url(), 401)))

    def test_a_2xx_save_with_a_failed_reread_is_saved_unverified_after_one_retry(self):
        (result, _r), page = self.run_edit(confirm_write=True, results=[result_of(self.before), None, None])
        self.assertEqual(result, "renewal_saved_unverified")
        self.assertEqual(page.clicked.count("Confirm"), 1)

    def test_target_identification_fails_closed(self):
        cases = (
            ([result_of()], "renewal_tenant_not_found"),
            ([result_of(dict(self.before, id="y" * 24))], "renewal_tenant_not_found"),
            ([result_of(self.before, tenant_row(id="x" * 24, accountUuid="c" * 32), total=2)] * 2,
             "renewal_row_ambiguous"),  # still two rows after narrowing by the row's own domain
            ([result_of(dict(self.before, accountDomain="other.com"))], "renewal_domain_mismatch"),
            ([result_of(dict(self.before, accountName="Acme Holdings"))], "renewal_name_mismatch"),
            ([result_of(dict(self.before, isDeleted=True))], "renewal_tenant_deleted"),
            ([None], "renewal_readback_unavailable"),
        )
        for results, expected in cases:
            (result, _r), page = self.run_edit(confirm_write=True, results=results)
            self.assertEqual(result, expected)
            self.assertEqual([s for s in page.selectors if "data-am" in s], [])

    def test_non_dev_pages_and_bad_ids_are_refused_before_any_search(self):
        with patch.object(runner, "_search_tenants", side_effect=AssertionError("untouched")):
            page = FakePage(url="https://leonardo.app.pentera.io/backoffice/tenantManagement")
            self.assertEqual(runner.renew_tenant(page, source(), TENANT, ID, gate=ok_gate)[0],
                             "renewal_environment_not_supported")
            self.assertEqual(runner.renew_tenant(FakePage(), source(), TENANT, "x", gate=ok_gate)[0],
                             "renewal_environment_not_supported")

    def test_dry_run_never_clicks_a_value_control(self):
        (_result, _r), page = self.run_edit(confirm_write=False, results=[result_of(self.before)])
        self.assertEqual(set(page.clicked) - {"Cancel"}, {runner.SCAN_EXEC_ROW_MENU_SELECTOR, runner.SPYCLOUD_EDIT_SELECTOR}
                         | {s for s in page.clicked if s.startswith("tr")})


class HelperTests(unittest.TestCase):
    def test_confirm_has_no_start_date_fallback_in_edit_mode(self):
        with patch.object(runner, "_confirm_enabled", return_value=False), \
                patch.object(runner, "_fill_license_date", side_effect=AssertionError("must not re-pick the start")):
            plan = {"license_start": date(2026, 10, 6), "license_end": date(2027, 10, 15)}
            self.assertFalse(runner._ensure_confirm_enabled(object(), object(), plan, allow_start_fallback=False))
        with patch.object(runner, "_confirm_enabled", side_effect=[False, True]), \
                patch.object(runner, "_fill_license_date", return_value=True), \
                patch.object(runner, "_license_date_value", side_effect=[date(2026, 10, 5), date(2027, 10, 15)]):
            self.assertTrue(runner._ensure_confirm_enabled(object(), object(), plan))  # create mode unchanged

    def test_apply_spec_maps_helper_failures_to_reason_codes(self):
        page = object()
        date_spec = runner.FieldSpec("date", "license_end", "accountLicense.expirationDate", date(2027, 10, 15))
        with patch.object(runner, "_fill_license_date", return_value=True), \
                patch.object(runner, "_license_date_value", return_value=date(2027, 10, 15)):
            self.assertIsNone(runner._apply_spec(page, date_spec))
        with patch.object(runner, "_fill_license_date", return_value=True), \
                patch.object(runner, "_license_date_value", return_value=date(2027, 10, 14)):
            self.assertEqual(runner._apply_spec(page, date_spec), "renewal_value_mismatch")
        with patch.object(runner, "_fill_select", return_value="fill_form_schema_unavailable"):
            self.assertEqual(runner._apply_spec(page, runner.FieldSpec("select", "Type", "p", "x")), "renewal_field_unavailable")
        with patch.object(runner, "_fill_text_control", return_value=("fill_value_mismatch", None)) as fill:
            spec = runner.FieldSpec("list", "SubDomains (Comma Separated Values)", "subDomains", ("a.com", "b.com"))
            self.assertEqual(runner._apply_spec(page, spec), "renewal_value_mismatch")
            self.assertEqual(fill.call_args.args[2], "a.com, b.com")
        with patch.object(runner, "_set_checkbox", return_value=False):
            self.assertEqual(runner._apply_spec(page, runner.FieldSpec("checkbox", "phishingEnabled", "p", False)),
                             "renewal_field_unavailable")

    def test_row_menu_still_allows_only_details_and_edit(self):
        self.assertEqual(runner.ROW_ACTIONS_ALLOWED, {runner.SCAN_EXEC_DETAILS_SELECTOR, runner.SPYCLOUD_EDIT_SELECTOR})
        self.assertEqual(runner.RENEWAL_EDIT_SELECTOR, runner.SPYCLOUD_EDIT_SELECTOR)
        with self.assertRaises(ValueError):
            runner._open_row_action(FakePage(), TENANT, '[data-am="Button-Grid_Delete"]')


class RoutesTests(unittest.TestCase):
    def test_renewal_contracts_are_edit_mode_and_outside_the_create_routes(self):
        self.assertEqual(set(runner.RENEWAL_ROUTES), set(runner.RENEWAL_ENGINES))
        for engine, contract in runner.RENEWAL_ROUTES.items():
            self.assertEqual(contract.mode, "edit")
            self.assertNotIn(engine, runner.ROUTES)
        for contract in runner.ROUTES.values():
            self.assertEqual(contract.mode, "create")

    def test_the_create_runner_refuses_an_edit_contract(self):
        edit = runner.RENEWAL_ROUTES["case_6_renew_both"]
        with patch.dict(runner.ROUTES, {"case_6_renew_both": edit}), \
                patch.object(runner, "_finish", side_effect=lambda ref, rev, result: result), \
                patch.object(runner, "_run", side_effect=AssertionError("create path must not run")):
            self.assertEqual(runner.run("CO-0900", "rev", route="case_6_renew_both"), "route_unsupported")

    def test_renewal_engines_match_the_documented_cases_and_exclude_single_product_renewals(self):
        engines = {runner.renewal_case(*pair)[0] for pair in runner.RENEWAL_CASES}
        self.assertEqual(runner.RENEWAL_ENGINES, engines - {"surface_renewal", "ce_renewal"})


class RunRenewalTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: contextlib.nullcontext(object())
        package = types.ModuleType("playwright")
        package.sync_api = fake

        @contextlib.contextmanager
        def attended(_playwright):
            yield types.SimpleNamespace(on=lambda *a, **k: None, url=ORIGIN + "/backoffice/tenantManagement")
        self.stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": fake}))
        self.stack.enter_context(patch.object(runner, "RUN_LOG_PATH", directory / "log.json"))
        self.stack.enter_context(patch.object(runner, "_attended_page", attended))
        self.stack.enter_context(patch.object(runner, "_readback_ids", return_value=(ID, UUID)))
        self.stack.enter_context(patch.object(runner, "renewal_fill_source", return_value=source()))

    def test_production_and_other_environments_are_refused_before_anything_else(self):
        with patch.object(runner, "_readback_ids", side_effect=AssertionError("untouched")), \
                patch.object(runner, "renewal_fill_source", side_effect=AssertionError("untouched")):
            for env in ("prod", "prod-clone", "", "DEV"):
                self.assertEqual(runner.run_renewal("CO-0900", confirm_write=True, env_name=env),
                                 ("renewal_environment_not_supported", {}))

    def test_a_co_without_a_dev_readback_is_refused_and_production_ids_are_never_used(self):
        with patch.object(runner, "_readback_ids", return_value=None), \
                patch.object(runner, "renewal_fill_source", side_effect=AssertionError("untouched")):
            self.assertEqual(runner.run_renewal("CO-0900", confirm_write=True), ("renewal_not_onboarded", {}))
        # The source carries production ids (production_ids_present) and that must NOT stop the run.
        self.assertTrue(source().production_ids_present)
        with patch.object(runner, "renew_tenant", return_value=("renewal_dry_run_planned", {})) as call:
            self.assertEqual(runner.run_renewal("CO-0900")[0], "renewal_dry_run_planned")
        self.assertEqual(call.call_args.args[3], ID)  # the Dev readback id is the target
        self.assertEqual(call.call_args.kwargs["expected_uuid"], UUID)

    def test_route_and_source_refusals_are_returned_before_any_browser_work(self):
        for code in ("renewal_route_not_supported", "renewal_route_mismatch", "renewal_not_approved"):
            with patch.object(runner, "renewal_fill_source", side_effect=runner.SurfaceSourceError(code)), \
                    patch.object(runner, "_attended_page", side_effect=AssertionError("no browser")):
                self.assertEqual(runner.run_renewal("CO-0900", confirm_write=True), (code, {}))
        self.assertEqual(runner.run_renewal("not-a-co"), ("invalid_co_reference", {}))

    def test_default_is_a_dry_run_and_the_confirm_flag_and_tenant_override_pass_through(self):
        for kwargs, flag in (({}, False), ({"confirm_write": True}, True)):
            with patch.object(runner, "renew_tenant", return_value=("renewal_dry_run_planned", {"x": 1})) as call:
                self.assertEqual(runner.run_renewal("CO-0900", tenant_name_override="Acme  Mirror", **kwargs),
                                 ("renewal_dry_run_planned", {"x": 1}))
            self.assertIs(call.call_args.kwargs["confirm_write"], flag)
            self.assertEqual(call.call_args.args[2], "Acme Mirror")

    def test_session_expiry_and_login_timeout_are_reported(self):
        with patch.object(runner, "renew_tenant", side_effect=runner.LeonardoSessionExpired()):
            self.assertEqual(runner.run_renewal("CO-0900", confirm_write=True)[0], "leonardo_session_expired")
        with patch.object(runner, "renew_tenant", side_effect=runner.LoginTimeout()):
            self.assertEqual(runner.run_renewal("CO-0900")[0], "development_login_timeout")


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, directory, ignore_errors=True)
        patcher = patch.object(runner, "RENEWAL_OUTCOMES_PATH", directory / "outcomes.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def main(self, *argv):
        with patch.object(sys, "argv", ["runner", *argv]), patch("builtins.print") as printed:
            code = runner.main()
        return code, json.loads(printed.call_args.args[0])

    def test_dry_run_is_the_default_and_confirm_write_is_explicit(self):
        with patch.object(runner, "run_renewal", return_value=("renewal_dry_run_planned", {"changes": []})) as call:
            _code, report = self.main("--co", "CO-0900", "--renew")
            self.assertEqual(call.call_args.kwargs, {"confirm_write": False, "env_name": "dev", "tenant_name_override": None})
            self.assertEqual(report["leonardo_write"], "not_performed")
            self.assertEqual(report["salesforce_writeback"], "not_performed")
            self.main("--co", "CO-0900", "--renew", "--confirm-write", "--tenant-name", "Acme Mirror")
            self.assertEqual(call.call_args.kwargs, {"confirm_write": True, "env_name": "dev",
                                                     "tenant_name_override": "Acme Mirror"})
            self.main("--co", "CO-0900", "--renew", "--confirm-write", "--env", "prod")
            self.assertEqual(call.call_args.kwargs["env_name"], "prod")  # run_renewal refuses it

    def test_verified_write_is_labelled_and_usage_errors(self):
        with patch.object(runner, "run_renewal", return_value=("renewal_edit_verified", {})):
            _code, report = self.main("--co", "CO-0900", "--renew", "--confirm-write")
        self.assertEqual(report["leonardo_write"], "verified")
        for argv in (["--renew"], ["--co", "CO-0900", "--renew", "--spycloud-off"], ["--co", "CO-0900", "--confirm-write"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.main(*argv)

    def test_a_run_that_stopped_before_confirm_never_claims_a_write(self):
        for result in ("renewal_dry_run_planned", "renewal_new_domain_in_production", "renewal_tenant_not_found",
                       "renewal_confirm_disabled", "playwright_runtime_unavailable"):
            self.assertEqual(runner.renewal_write_label(result, True), "not_performed")
        self.assertEqual(runner.renewal_write_label("renewal_save_failed", False), "not_performed")
        self.assertEqual(runner.renewal_write_label("renewal_saved_unverified", True), "attempted_unverified")


if __name__ == "__main__":
    unittest.main()
