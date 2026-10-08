"""Case 5 renewal (owner decisions 2026-10-07, Guru G12 T1-T7): Surface is ADDED to an existing CE tenant.

Fully synthetic and offline: placeholder account, domains and DealHub rows; every page is the recording fake of the
SpyCloud tests; the Edit form is a small stateful stand-in so the AFTER row is built from what the runner applied.
Nothing reaches Salesforce, Leonardo, Redash or the network.
"""

from __future__ import annotations

import contextlib
import copy
import json
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import renewal_mirror as mirror
from integration.tests.test_leonardo_inventory import page as inventory_page, tenant_row as prod_tenant_row
from integration.tests.test_renewal_edit import (
    CE_DOMAIN, CLEAR, DOMAIN, RENEW, SCE, TENANT, TODAY, SourceTests, _row, ms, put, result_of, source as case6_source,
    tenant_row)
from integration.tests.test_renewal_run import REF, REV, RunnerSandbox
from integration.tests.test_spycloud_off import FakePage, ID, UUID, _Response, edit_url

CASE5_TYPE = "Renewal of Credential Exposure Module + New Surface Product"
ENGINE = runner.RENEWAL_CASE5_ENGINE
TERM_END = date(2029, 10, 15)
SELECT_ROW = {"Weekly": "WEEKLY", "Monthly": "MONTHLY", "None": "NO_SCHEDULE"}
SCAN_LABEL = runner.SURFACE_MAX_SCAN_DURATION_LABEL

PROFILE_TARGET = {
    "subDomainsReconEnabled": True, "webDictionaryBruteForceEnabled": True, "fullNucleiScanEnabled": True,
    "automatedDiscoveryEnabled": False, "webDorkingEnabled": False, "aiEnabled": False, "staticOutboundIpEnabled": False,
    "authenticatedTestingEnabled": False, "multipleAttackStacksEnabled": False,
    "notificationsAllowed": True, "multipleUsersAllowed": True, "apiAccessAllowed": True, "phishingEnabled": False,
    "provisioningEnabled": True, "leakedCredentialsAllowed": True}


def dealhub(prime=True, legacy=True, go=False):
    rows = []
    if legacy:  # an expired legacy CE row: ignored
        rows.append(_row("Pentera Core Plus Commercial - 500 End Points", "Expired", "2023-10-16", "2026-10-15"))
    if prime:
        rows.append(_row("Pentera Surface Prime - 1000 Subdomains", "Pending", "2026-10-16", "2029-10-15"))
    if go:
        rows.append(_row("Pentera Surface Go - 500 Subdomains", "Pending", "2026-10-16", "2029-10-15"))
    rows.append(_row("Pentera Core Plus Commercial - 500 End Points", "Pending", "2026-10-16", "2029-10-15"))
    return rows


def case5_source(rows=None, alternates="acme.net, app.acme.com"):
    """The Case 5 renewal source through the real fixed-field read (Salesforce and DealHub stubbed)."""
    co = {"Name": "CO-0900", "LastModifiedDate": "2026-10-05T10:00:00.000+0000", "Account__c": "0015g00000AbCdE",
          "Account_Name__c": TENANT, "Account_Country__c": "United States", "Main_Domain__c": DOMAIN,
          "Alternate_Domains__c": alternates, "Email_Domains__c": CE_DOMAIN, "Onboarding_Product__c": SCE,
          "Onboarding_Type__c": CASE5_TYPE, "Surface_Account_ID__c": "", "Account_UUID__c": "",
          "Onboarding_Approval_Status__c": "Approved"}
    with patch.object(runner, "_sf_records", side_effect=[[co], rows if rows is not None else dealhub()]), \
            patch.object(runner, "_run_day", return_value=TODAY):
        return runner.renewal_fill_source("CO-0900")


def ce_only_row(**overrides):
    """A pure CE-only tenant before the renewal: LC allowed ON with one scanned domain, CE-style toggles OFF, no Surface limits."""
    row = tenant_row(alternateDomains=[], subDomains=[], leakedCredentialsScannedDomains=[CE_DOMAIN],
                     leakedCredentialsScanningInterval="WEEKLY", campaignsTimeoutInHours=24)
    for key, path in runner.VALIDATION_TOGGLE_PATHS.items():
        if key != "mfaRequired":
            put(row, path, key == "leakedCredentialsAllowed")
    put(row, "accountLicense.assetsNumber", 100)
    put(row, "accountLicense.domainsNumber", 1)
    put(row, "accountLicense.subDomainsNumber", 0)
    for path, value in overrides.items():
        put(row, path.replace("__", "."), value)
    return row


def readable(*keys):
    """Rows whose Edit-only profile paths ARE reported (so they are compared with the row, not form-verified)."""
    return {key: True for key in keys}


class Case5Harness(unittest.TestCase):
    """renew_tenant with a stateful Edit form: applying a spec changes the form; a save commits the form to the row."""

    row_reports_form_only = False  # the default: the search row reports neither subDomainsNumberAllowed nor (in some tests) the duration

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.before = ce_only_row()
        self.applied: list = []
        self.form: dict = {}
        self.form_defaults = {"subDomainsNumberAllowed": False, SCAN_LABEL: 24}
        self.expand_calls: list = []
        self.expand_ok = True
        self.searches = 0

        def apply(page, spec):
            self.applied.append(spec)
            self.form[spec.key] = spec.target
            return None

        def expand(page, keys):
            self.expand_calls.append(sorted(keys))
            return self.expand_ok

        def search(page, search, query):
            self.searches += 1
            return result_of(self.before if self.searches == 1 else self.committed())

        self.stack.enter_context(patch.object(runner, "_apply_spec", side_effect=apply))
        self.stack.enter_context(patch.object(runner, "_expand_renewal_advanced", side_effect=expand))
        self.stack.enter_context(patch.object(runner, "_license_date_value", side_effect=lambda page, key: date(2025, 10, 1)))
        self.stack.enter_context(patch.object(runner, "_ensure_confirm_enabled", return_value=True))
        self.stack.enter_context(patch.object(runner, "_run_day", return_value=TODAY))
        self.stack.enter_context(patch.object(runner, "_search_tenants", side_effect=search))
        self.form_current = self.stack.enter_context(patch.object(runner, "_form_current", side_effect=self.read_form))

    def read_form(self, page, spec):
        if spec.key in self.form:
            return self.form[spec.key]
        value = runner._row_value(self.before, spec.path)
        if value is None:
            return self.form_defaults.get(spec.key)
        if spec.kind == "date":
            return date.fromisoformat(sorted(runner._epoch_dates(value))[0])
        if spec.kind == "select":
            return {"NO_SCHEDULE": "None", "WEEKLY": "Weekly", "MONTHLY": "Monthly"}.get(value, value)
        return value

    def committed(self, drop=()):
        """The row after a save of everything applied; paths the row never reported stay unreported."""
        row = copy.deepcopy(self.before)
        for spec in self.applied:
            if runner._row_value(self.before, spec.path) is None and spec.profile and not self.row_reports_form_only:
                continue
            if spec.key in drop:
                continue
            if spec.kind == "date":
                value = ms(spec.target)
            elif spec.kind == "select":
                value = SELECT_ROW.get(spec.target, str(spec.target).upper().replace(" ", "_"))
            elif spec.kind == "list":
                value = list(spec.target)
            else:
                value = spec.target
            put(row, spec.path, value)
        return row

    def run_edit(self, *, confirm_write, src=None, gate=lambda domains: dict(CLEAR), page=None):
        page = page or FakePage(checked=False, response=_Response(edit_url()))
        return runner.renew_tenant(page, src or case5_source(), TENANT, ID, expected_uuid=UUID, gate=gate,
                                   confirm_write=confirm_write, today=TODAY), page


class SourceAndSpecTests(unittest.TestCase):
    def test_the_source_reads_the_prime_term_ignores_the_expired_legacy_ce_row_and_is_case_5(self):
        read = case5_source()
        self.assertEqual(read.engine, ENGINE)
        self.assertEqual(read.licensed_subdomains, 1000)
        self.assertEqual(read.surface_term["scanning_interval"], "Weekly")
        self.assertEqual(runner.renewal_expiration(read), TERM_END)
        self.assertEqual(read.surface_term["assets"], 10000)  # owner: keep 10,000, never 50,000
        self.assertEqual(case5_source(rows=dealhub(legacy=False)).licensed_subdomains, 1000)

    def test_a_go_tier_scans_monthly(self):
        read = case5_source(rows=dealhub(prime=False, go=True, legacy=False))
        self.assertEqual(read.surface_term["tier"], "go")
        self.assertEqual(read.surface_term["scanning_interval"], "Monthly")
        specs, _added, errors = runner.build_renewal_specs(read, ce_only_row())
        self.assertEqual(errors, [])
        interval = next(spec for spec in specs if spec.key == "Scanning interval")
        self.assertEqual(interval.target, "Monthly")
        self.assertEqual(next(spec for spec in specs if spec.key == "Number of domains").target, 500)

    def test_case_5_specs_carry_the_limits_the_domains_value_and_the_whole_profile(self):
        specs, added, errors = runner.build_renewal_specs(case5_source(), ce_only_row())
        self.assertEqual(errors, [])
        by_key = {spec.key: spec for spec in specs}
        self.assertEqual(by_key["Number of assets"].target, 10000)
        self.assertEqual(by_key["Number of subdomains"].target, 1000)
        self.assertEqual(by_key["Number of domains"].target, 1000)  # Case 5 only: = licensed subdomains
        self.assertEqual(by_key["Type"].target, "Prepaid annual subscription")
        self.assertEqual(by_key["Scanning interval"].target, "Weekly")
        self.assertEqual(by_key["Leaked Credentials scanning interval"].target, "Weekly")
        self.assertNotIn(runner.LC_SCANNED_DOMAINS_LABEL, by_key)  # the CE domain is already scanned: nothing to add
        lc_missing, _a, _e = runner.build_renewal_specs(case5_source(), ce_only_row(leakedCredentialsScannedDomains=[]))
        lc_spec = next(spec for spec in lc_missing if spec.key == runner.LC_SCANNED_DOMAINS_LABEL)
        self.assertEqual(lc_spec.target, (CE_DOMAIN,))  # kept per entitlement (LC ON, Weekly, CE email domain)
        self.assertNotIn("license_start", by_key)
        for key, want in PROFILE_TARGET.items():
            self.assertIs(by_key[key].target, want, key)
        self.assertEqual(by_key[SCAN_LABEL].target, 90)
        self.assertIs(by_key["subDomainsNumberAllowed"].target, True)
        self.assertNotIn("scan_now", by_key)  # Scan now is never set (mirrors Case 1 with a schedule)
        self.assertEqual(sum(1 for spec in specs if spec.profile), 15)  # 9 advanced + 4 + subdomains flag + duration
        self.assertEqual(added, {"acme.net", "app.acme.com"})  # the CE email domain is already on the tenant

    def test_cases_4_and_6_keep_the_domains_rule_and_stay_report_only(self):
        for engine in ("case_4_renew_surface_new_ce", "case_6_renew_both"):
            with self.subTest(engine=engine):
                src = case6_source(engine=engine)
                row = tenant_row()
                specs, _added, errors = runner.build_renewal_specs(src, row)
                self.assertEqual(errors, [])
                keys = {spec.key for spec in specs}
                self.assertNotIn("Number of domains", keys)
                self.assertFalse(any(spec.profile for spec in specs))
                self.assertNotIn("fullNucleiScanEnabled", keys)
                self.assertNotIn(SCAN_LABEL, keys)
                self.assertEqual(runner.renewal_domains_check(src, row)["result"], "match")
                added = tenant_row(alternateDomains=["acme.net"])
                changed = case6_source(engine=engine, alternate_domains=("acme.net", "new-root.com"))
                self.assertEqual(runner.renewal_domains_check(changed, added)["result"], "mismatch")  # equal sets only


class DomainsRuleTests(Case5Harness):
    def test_tenant_roots_must_all_be_in_salesforce_and_salesforce_extras_are_added(self):
        src = case5_source()  # Salesforce roots: acme.com + acme.net
        ok = runner.renewal_domains_check(src, ce_only_row())
        self.assertEqual(ok, {"result": "match", "count": 2, "added_roots": 1})
        same = runner.renewal_domains_check(src, ce_only_row(alternateDomains=["acme.net"]))
        self.assertEqual((same["result"], same["added_roots"]), ("match", 0))
        extra = runner.renewal_domains_check(src, ce_only_row(alternateDomains=["acme.net", "tenant-only.com"]))
        self.assertEqual(extra["result"], "mismatch")
        self.assertEqual(extra["only_on_tenant"], ["tenant-only.com"])
        self.assertEqual(runner.renewal_domains_check(src, ce_only_row(alternateDomains=None))["result"], "unreadable")

    def test_a_tenant_root_missing_from_salesforce_stops_before_the_edit_form(self):
        self.before = ce_only_row(alternateDomains=["tenant-only.com"])
        (result, report), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_domains_mismatch_manual_review")
        self.assertEqual(report["domains_check"], {"result": "mismatch", "only_in_salesforce": 1, "only_on_tenant": 1})
        self.assertEqual([s for s in page.selectors if "data-am" in s], [])
        self.assertEqual(self.applied, [])
        self.assertNotIn("tenant-only.com", json.dumps({k: v for k, v in report.items() if k != "manual_review"}))


class Case5EditTests(Case5Harness):
    def test_dry_run_plans_the_profile_and_domains_cancels_and_writes_nothing(self):
        (result, report), page = self.run_edit(confirm_write=False)
        self.assertEqual(result, "renewal_dry_run_planned")
        self.assertEqual(self.applied, [])
        self.assertNotIn("Confirm", page.clicked)
        self.assertIn("Cancel", page.clicked)
        self.assertTrue(report["surface_added"])
        self.assertEqual(report["profile_drift"], [])  # applied for Case 5, not report-only
        self.assertEqual(report["profile"]["controls"], 15)
        self.assertEqual(report["profile"]["form_verified_only"], 1)  # subDomainsNumberAllowed: not in the row
        fields = {change["field"]: change for change in report["changes"]}
        self.assertEqual(fields["Number of domains"]["target"], 1000)
        self.assertEqual(fields[SCAN_LABEL]["current"], 24)
        self.assertEqual(fields[SCAN_LABEL]["target"], 90)
        self.assertEqual(fields["subDomainsNumberAllowed"]["current"], False)  # read from the form
        self.assertEqual(report["domains_check"]["added_roots"], 1)
        # The advanced toggles that must change (the three that are OFF on a CE-only tenant) are located, or it fails closed.
        self.assertEqual(self.expand_calls, [["fullNucleiScanEnabled", "subDomainsReconEnabled", "webDictionaryBruteForceEnabled"]])
        self.assertNotIn(CE_DOMAIN, json.dumps(report))

    def test_confirm_write_applies_the_whole_profile_and_the_after_row_is_the_production_shape(self):
        (result, report), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_edit_verified")
        self.assertEqual(page.clicked.count("Confirm"), 1)
        self.assertEqual(report["readback"]["changed_bad"], [])
        self.assertEqual(report["readback"]["preserved_bad"], [])
        after = self.committed()
        lic = after["accountLicense"]
        # The anonymised production example CO-0462: assets 10,000, domains = subdomains = 1000, Weekly, LC kept.
        self.assertEqual((lic["assetsNumber"], lic["domainsNumber"], lic["subDomainsNumber"]), (10000, 1000, 1000))
        self.assertEqual(after["scanningInterval"], "WEEKLY")
        self.assertTrue(lic["leakedCredentialsAllowed"])
        self.assertEqual(after["leakedCredentialsScanningInterval"], "WEEKLY")
        self.assertEqual(after["leakedCredentialsScannedDomains"], [CE_DOMAIN])
        self.assertEqual(lic["licenseType"], "PREPAID_ANNUAL_SUBSCRIPTION")
        self.assertEqual(sorted(runner._epoch_dates(lic["expirationDate"])), [TERM_END.isoformat()])
        self.assertEqual(lic["startDate"], self.before["accountLicense"]["startDate"])  # start never changed
        self.assertNotIn("license_start", [spec.key for spec in self.applied])
        for key, want in PROFILE_TARGET.items():
            self.assertIs(runner._row_value(after, runner.VALIDATION_TOGGLE_PATHS[key]), want, key)
        self.assertEqual(after["campaignsTimeoutInHours"], 90)
        self.assertEqual(self.form["subDomainsNumberAllowed"], True)  # applied and re-read through the form
        self.assertEqual(sorted(after["alternateDomains"]), ["acme.net"])
        self.assertEqual(sorted(after["subDomains"]), ["app.acme.com"])
        self.assertFalse(after["leakedCredentialsSettings"]["spyCloudSettings"]["enabled"])  # preserved; no SpyCloud step follows (2026-10-08)
        self.assertEqual(self.applied[0].kind, "checkbox")  # checkboxes first

    def test_a_row_that_reports_the_duration_and_the_subdomains_flag_is_compared_with_the_row(self):
        self.row_reports_form_only = True
        put(self.before, runner.RENEWAL_SUBDOMAINS_ALLOWED_PATH, False)
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_edit_verified")
        self.assertEqual(report["profile"]["form_verified_only"], 0)
        self.assertIs(runner._row_value(self.committed(), runner.RENEWAL_SUBDOMAINS_ALLOWED_PATH), True)

    def test_a_search_row_without_the_duration_is_form_verified_and_a_wrong_duration_in_the_form_is_set(self):
        self.before["campaignsTimeoutInHours"] = None
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_edit_verified")
        self.assertEqual(report["profile"]["form_verified_only"], 2)
        self.assertEqual(self.form[SCAN_LABEL], 90)
        self.assertIn(SCAN_LABEL, [spec.key for spec in self.applied])

    def test_a_form_only_control_that_does_not_keep_its_value_stops_before_confirm(self):
        original = self.form_current.side_effect

        def stuck(page, spec):  # the toggle reads back OFF after being set
            return False if spec.key == "subDomainsNumberAllowed" else original(page, spec)

        self.form_current.side_effect = stuck
        (result, _report), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_value_mismatch")
        self.assertNotIn("Confirm", page.clicked)

    def test_every_profile_control_is_verified_after_the_save_not_only_the_changed_ones(self):
        # A toggle that took is fine; one the save reverted is a mismatch, whether or not it was in the diff.
        self.before = ce_only_row()
        put(self.before, runner.VALIDATION_TOGGLE_PATHS["fullNucleiScanEnabled"], True)  # already correct: not in the diff
        original = self.committed

        def flipped(drop=()):
            row = original(drop)
            put(row, runner.VALIDATION_TOGGLE_PATHS["fullNucleiScanEnabled"], False)  # the save reverts it
            return row

        self.committed = flipped
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_readback_changed_mismatch")
        self.assertEqual(report["readback"]["changed_bad"], ["fullNucleiScanEnabled"])
        self.assertEqual(runner.renewal_write_label(result, True), "attempted_unverified")

    def test_a_changed_profile_toggle_that_did_not_take_is_a_mismatch(self):
        original = self.committed
        self.committed = lambda drop=(): original(("aiEnabled", "webDorkingEnabled", "automatedDiscoveryEnabled"))
        self.before["aiEnabled"] = True
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_readback_changed_mismatch")
        self.assertEqual(report["readback"]["changed_bad"], ["aiEnabled"])

    def test_the_duration_the_row_reports_must_match_after_the_save(self):
        original = self.committed
        self.committed = lambda drop=(): original((SCAN_LABEL,))  # the row still says 24
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_readback_changed_mismatch")
        self.assertEqual(report["readback"]["changed_bad"], [SCAN_LABEL])

    def test_missing_advanced_controls_fail_closed_with_renewal_field_unavailable(self):
        self.expand_ok = False
        (result, _r), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_field_unavailable")
        self.assertEqual(self.applied, [])
        self.assertNotIn("Confirm", page.clicked)
        self.assertIn("Cancel", page.clicked)
        self.expand_ok = True
        self.form_current.side_effect = lambda page, spec: None if spec.profile else self.read_form(page, spec)
        (result, _r), _page = self.run_edit(confirm_write=False)
        self.assertEqual(result, "renewal_field_unavailable")
        runner._apply_spec.side_effect = lambda page, spec: "renewal_field_unavailable" if spec.profile else None
        self.form_current.side_effect = self.read_form
        (result, _r), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_field_unavailable")
        self.assertNotIn("Confirm", page.clicked)

    def test_an_added_root_domain_goes_through_the_production_gate_like_subdomains(self):
        seen = []
        self.run_edit(confirm_write=False, gate=lambda domains: seen.append(domains) or dict(CLEAR))
        self.assertEqual(seen, [["acme.net", "app.acme.com"]])  # the CE email domain is already on the tenant
        blocked = lambda domains: {"result": "renewal_new_domain_in_production", "blocks": True, "matches": [{"id": "p2"}]}
        (result, _r), page = self.run_edit(confirm_write=True, gate=blocked)
        self.assertEqual(result, "renewal_new_domain_in_production")
        self.assertEqual([s for s in page.selectors if "data-am" in s], [])

    def test_a_fully_renewed_tenant_whose_row_reports_everything_never_opens_the_edit_form(self):
        self.row_reports_form_only = True
        put(self.before, runner.RENEWAL_SUBDOMAINS_ALLOWED_PATH, False)
        self.run_edit(confirm_write=True)
        self.before = self.committed()
        self.applied.clear()
        self.searches = 0
        (result, report), page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_already_current")
        self.assertEqual(report["changes"], [])
        self.assertEqual([s for s in page.selectors if "data-am" in s], [])

    def test_form_only_controls_are_re_checked_in_the_form_and_a_current_form_is_already_current(self):
        self.run_edit(confirm_write=True)
        self.before = self.committed()  # the row still cannot report two controls
        self.before["campaignsTimeoutInHours"] = None
        self.applied.clear()
        self.searches = 0
        (result, report), page = self.run_edit(confirm_write=True)  # the form holds 90 / ON from the run above
        self.assertEqual(result, "renewal_already_current")
        self.assertEqual(self.applied, [])
        self.assertNotIn("Confirm", page.clicked)
        self.assertIn("Cancel", page.clicked)  # the form was opened to read it, then closed unsaved

    def test_a_kept_expiration_still_applies_the_profile(self):
        self.before = ce_only_row(accountLicense__expirationDate=ms(date(2030, 1, 1)))
        (result, report), _page = self.run_edit(confirm_write=True)
        self.assertEqual(result, "renewal_edit_verified")
        self.assertEqual(report["expiration"], "kept_already_current_or_later")
        self.assertNotIn("license_end", [spec.key for spec in self.applied])
        self.assertEqual(self.form[SCAN_LABEL], 90)


class Case4And6UnchangedTests(Case5Harness):
    def setUp(self):
        super().setUp()
        self.before = tenant_row()

    def test_case_6_keeps_the_domains_value_and_reports_profile_drift_without_applying_it(self):
        src = case6_source()
        put(self.before, runner.VALIDATION_TOGGLE_PATHS["aiEnabled"], True)
        (result, report), _page = self.run_edit(confirm_write=False, src=src)
        self.assertEqual(result, "renewal_dry_run_planned")
        self.assertIn("aiEnabled", report["profile_drift"])
        self.assertNotIn("surface_added", report)
        self.assertNotIn("Number of domains", {change["field"] for change in report["changes"]})
        self.assertEqual(self.expand_calls, [])  # the advanced section is never opened for Cases 4 and 6

    def test_case_4_and_6_apply_never_touches_the_profile_or_the_duration(self):
        for engine in ("case_4_renew_surface_new_ce", "case_6_renew_both"):
            self.applied.clear()
            self.form.clear()
            self.searches = 0
            (result, _report), _page = self.run_edit(confirm_write=True, src=case6_source(engine=engine))
            self.assertEqual(result, "renewal_edit_verified", engine)
            self.assertFalse(any(spec.profile for spec in self.applied))
            self.assertNotIn(SCAN_LABEL, [spec.key for spec in self.applied])
            self.assertNotIn("Number of domains", [spec.key for spec in self.applied])

    def test_case_6_with_different_root_sets_still_needs_manual_review(self):
        self.before = tenant_row(alternateDomains=[])  # Salesforce has acme.net, the tenant does not
        (result, _report), _page = self.run_edit(confirm_write=True, src=case6_source())
        self.assertEqual(result, "renewal_domains_mismatch_manual_review")


class MirrorAndOrchestratorTests(RunnerSandbox):
    def test_the_mirror_of_a_ce_only_production_tenant_is_a_valid_before_for_case_5(self):
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        row = prod_tenant_row(1, accountName=TENANT, accountDomain=DOMAIN, alternateDomains=[], subDomains=[],
                              scanningInterval="NONE", userEmailDomains=[CE_DOMAIN],
                              accountLicense={**prod_tenant_row(1)["accountLicense"], "licenseType": "prepaid annual subscription",
                                              "assetsNumber": 100, "domainsNumber": 1, "subDomainsNumber": 0,
                                              "leakedCredentialsAllowed": True, "leakedCredentialsScannedDomainsNumber": 1,
                                              "startDate": 1758000000000, "expirationDate": 1790000000000})
        from integration.onboarding import leonardo_inventory as inventory

        assembled = inventory.assemble_pages([inventory_page(0, [row], 1, size=1)], page_size=1)
        payload = inventory.snapshot_payload(inventory.ENVIRONMENTS["prod-clone"], assembled, now)
        plan = mirror.plan_from_payload(payload, (TENANT,), (DOMAIN,), (CE_DOMAIN,), TODAY)
        self.assertEqual((plan.scanning_interval, plan.leaked_credentials_allowed, plan.subdomains), ("None", True, 0))
        self.assertEqual(plan.lc_domains, (CE_DOMAIN,))
        self.assertEqual(plan.license_type, "Prepaid annual subscription")
        before = ce_only_row(accountLicense__assetsNumber=plan.assets, accountLicense__domainsNumber=plan.domains,
                             accountLicense__subDomainsNumber=plan.subdomains, accountDomain=plan.primary_domain,
                             accountName=plan.tenant_name, leakedCredentialsScannedDomains=list(plan.lc_domains))
        specs, added, errors = runner.build_renewal_specs(case5_source(), before)
        self.assertEqual(errors, [])
        changes, error = runner.plan_renewal_changes(before, specs, TODAY)
        self.assertIsNone(error)
        keys = {change["spec"].key for change in changes}
        self.assertTrue({"Number of domains", "Number of subdomains", "Scanning interval", "license_end",
                         "fullNucleiScanEnabled", "provisioningEnabled"} <= keys)
        self.assertEqual(runner.renewal_domains_check(case5_source(), before)["result"], "match")
        self.assertEqual(added, {"acme.net", "app.acme.com"})

    def test_no_spycloud_step_after_a_case_5_renewal_but_the_manual_tool_accepts_the_route(self):
        self.assertIn(ENGINE, runner.SPYCLOUD_MIRROR_ROUTES)  # the manual --spycloud-off tool may still target it
        self.source = SimpleNamespace(source_revision=REV, engine=ENGINE)
        self.stub()
        runner.record_runner_start(REF, REV, "2026-10-07T09:00:00", route=ENGINE)
        self.assertEqual(runner.run_renewal_onboarding(REF, REV), "renewal_edit_verified")
        self.assertEqual(self.steps(), ["dry", "outcome", "apply", "outcome"])  # SpyCloud stays ON (owner 2026-10-08)

    def test_the_spycloud_mirror_target_accepts_a_case_5_co(self):
        record = {REF: {"mirror_of_production": True, "status": "verified"}}
        co = [{"Name": REF, "Account_Name__c": TENANT, "Onboarding_Product__c": SCE, "Onboarding_Type__c": CASE5_TYPE}]
        with patch.object(runner, "_mirror_records", return_value=record), patch.object(runner, "_sf_records", return_value=co):
            self.assertEqual(runner._spycloud_mirror_target(REF)[0], ENGINE)


class DashboardTests(unittest.TestCase):
    def ctx(self, engine):
        term = {"start": "2026-10-16", "end": "2029-10-15", "apply_from": "2026-10-02", "applicable_now": True,
                "subdomains": 1000}
        case = next(case for case in runner.RENEWAL_CASES.values() if case[0] == engine)
        return SimpleNamespace(case=case, mirror=True, outcome=None, stage_state={},
                               plan_data=(True, {"surface_term": term, "ce_term": None}))

    def test_the_case_5_summary_shows_the_profile_and_domains_equal_subdomains(self):
        html = dashboard._renewal_plan_summary(self.ctx(ENGINE))
        self.assertIn("profile applied, domains = subdomains (1000)", html)
        self.assertNotIn("Number of domains", html)
        other = dashboard._renewal_plan_summary(self.ctx("case_6_renew_both"))
        self.assertIn("Number of domains", other)
        self.assertNotIn("profile applied", other)

    def test_the_operator_reminder_is_a_plain_to_do_item_with_no_action(self):
        html = dashboard._renewal_operator_check()
        self.assertIn("Check the Operator Account (did the customer&#x27;s TA change?)", html)
        self.assertNotIn("<form", html)
        self.assertNotIn("<button", html)


if __name__ == "__main__":
    unittest.main()
