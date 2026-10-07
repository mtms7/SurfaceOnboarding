"""Owner decisions 2026-10-07: renewal Number-of-domains rule, scan status for renewal COs, hidden search for read-only paths.

No network, no browser, no Salesforce: every page, Salesforce read and state file is a fake or a temporary one.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import tools.attended_ce_only_playwright as runner
from integration.tests.test_renewal_edit import (
    CE_DOMAIN, DOMAIN, TENANT, TODAY, after_row, ok_gate, result_of, source, tenant_row)
from integration.tests.test_spycloud_off import FakePage, ID, UUID, _Response, edit_url

MISMATCH = "renewal_domains_mismatch_manual_review"
CO0767_SF = ("a2a.eu", "a2a.it a2aenergia.eu unareti.it gruppoa2a.it")
CO0767_ROOTS = ["a2a.eu", "a2a.it", "a2aenergia.eu", "unareti.it", "gruppoa2a.it"]


def co0767_source():
    main, roots, subdomains = runner.classify_surface_domains(*CO0767_SF)
    return source(main_domain=main, alternate_domains=roots, subdomains=subdomains)


def co0767_row(**overrides):
    """The tenant already carries the CO's five roots (primary + four alternates) and licence domains = 10."""
    values = {"accountDomain": "a2a.eu", "alternateDomains": CO0767_ROOTS[1:], "accountLicense__domainsNumber": 10}
    values.update(overrides)
    return tenant_row(**values)


class DomainsCheckTests(unittest.TestCase):
    def check(self, row, src=None):
        return runner.renewal_domains_check(src or co0767_source(), row)

    def test_the_co0767_shape_matches_and_counts_roots(self):
        src = co0767_source()
        self.assertEqual((src.main_domain, sorted(src.alternate_domains)),
                         ("a2a.eu", sorted(CO0767_ROOTS[1:])))  # space-separated alternates are split
        self.assertEqual(self.check(co0767_row()), {"result": "match", "count": 5})

    def test_case_order_duplicates_and_a_repeated_main_domain_still_match(self):
        row = co0767_row(alternateDomains=["Unareti.IT.", "gruppoa2a.it", "A2A.IT", "a2aenergia.eu", "a2a.eu", "a2a.it"])
        self.assertEqual(self.check(row), {"result": "match", "count": 5})
        src = source(main_domain="Acme.com", alternate_domains=("ACME.net", "acme.com", "acme.net"))
        self.assertEqual(runner.renewal_domains_check(src, tenant_row(alternateDomains=["acme.net", "acme.com"])),
                         {"result": "match", "count": 2})

    def test_an_extra_domain_on_either_side_is_a_mismatch_with_counts_and_lists(self):
        extra_tenant = self.check(co0767_row(alternateDomains=[*CO0767_ROOTS[1:], "old.example"]))
        self.assertEqual(extra_tenant, {"result": "mismatch", "only_in_salesforce": [], "only_on_tenant": ["old.example"]})
        missing = self.check(co0767_row(alternateDomains=CO0767_ROOTS[2:]))
        self.assertEqual(missing, {"result": "mismatch", "only_in_salesforce": ["a2a.it"], "only_on_tenant": []})
        both = self.check(co0767_row(alternateDomains=["a2a.it", "other.example", "x.example"]))
        self.assertEqual((len(both["only_in_salesforce"]), len(both["only_on_tenant"])), (3, 2))

    def test_an_unreadable_tenant_domain_list_is_unreadable(self):
        for row in (co0767_row(alternateDomains=None), co0767_row(alternateDomains="a2a.it"),
                    co0767_row(alternateDomains=["a2a.it", 7]), co0767_row(accountDomain=None)):
            self.assertEqual(self.check(row), {"result": "unreadable"})

    def test_specs_never_plan_number_of_domains(self):
        specs, _added, errors = runner.build_renewal_specs(co0767_source(), co0767_row())
        self.assertEqual(errors, [])
        self.assertNotIn("Number of domains", {spec.key for spec in specs})


class RenewDomainsTests(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.applied: list[str] = []
        self.before = co0767_row()
        stack.enter_context(patch.object(runner, "_apply_spec", side_effect=lambda page, spec: self.applied.append(spec.key)))
        stack.enter_context(patch.object(runner, "_license_date_value", return_value=date(2025, 10, 1)))
        stack.enter_context(patch.object(runner, "_ensure_confirm_enabled", return_value=True))
        stack.enter_context(patch.object(runner, "_run_day", return_value=TODAY))
        stack.enter_context(patch.object(runner, "_form_current", side_effect=self.read_form))

    def read_form(self, page, spec):
        value = runner._row_value(self.before, spec.path)
        if spec.kind == "date":
            return date.fromisoformat(sorted(runner._epoch_dates(value))[0])
        if spec.kind == "select":
            return {"NO_SCHEDULE": "None", "WEEKLY": "Weekly"}.get(value, value)
        return value

    def run_edit(self, row, *, confirm_write=False, page=None, results=None):
        page = page or FakePage(checked=False, response=_Response(edit_url()))
        with patch.object(runner, "_search_tenants", side_effect=results or [result_of(row)]):
            outcome = runner.renew_tenant(page, co0767_source(), TENANT, ID, expected_uuid=UUID, gate=ok_gate,
                                          confirm_write=confirm_write, today=TODAY)
        return outcome, page

    def test_equal_sets_keep_the_current_number_of_domains_and_continue(self):
        row = dict(self.before, accountName=TENANT)
        (result, report), _page = self.run_edit(row)
        self.assertEqual(result, "renewal_dry_run_planned")
        self.assertEqual(report["domains_check"], {"result": "match", "count": 5})
        self.assertNotIn("Number of domains", {change["field"] for change in report["changes"]})
        self.assertNotIn("manual_review", report)

    def test_confirm_write_never_applies_number_of_domains_and_verifies_it_unchanged(self):
        after = after_row(self.before)  # the saved tenant: Number of domains still 10
        (result, _report), _page = self.run_edit(self.before, confirm_write=True,
                                                 results=[result_of(self.before), result_of(after)])
        self.assertEqual(result, "renewal_edit_verified")
        self.assertNotIn("Number of domains", self.applied)
        self.assertIn("accountLicense.domainsNumber", runner.RENEWAL_PRESERVE_PATHS)
        moved = after_row(self.before, accountLicense__domainsNumber=1000)  # a changed value would be caught
        (result, report), _page = self.run_edit(self.before, confirm_write=True,
                                                results=[result_of(self.before), result_of(moved)])
        self.assertEqual(result, "renewal_readback_preserved_mismatch")
        self.assertEqual(report["readback"]["preserved_bad"], ["accountLicense.domainsNumber"])

    def test_any_difference_stops_before_the_edit_form_opens_in_both_modes(self):
        for alternates, only_sf, only_tenant in (
                ([*CO0767_ROOTS[1:], "old.example"], 0, 1), (CO0767_ROOTS[2:], 1, 0), (["a2a.it", "x.example"], 3, 1)):
            for confirm in (False, True):
                row = co0767_row(alternateDomains=alternates)
                (result, report), page = self.run_edit(row, confirm_write=confirm)
                self.assertEqual(result, MISMATCH)
                self.assertEqual(report["domains_check"], {"result": "mismatch", "only_in_salesforce": only_sf,
                                                           "only_on_tenant": only_tenant})
                self.assertEqual([s for s in page.selectors if "data-am" in s], [])  # Edit never opened
                self.assertEqual(self.applied, [])
                self.assertEqual(runner.renewal_write_label(result, confirm), "not_performed")
                self.assertEqual(len(report["manual_review"]["only_in_salesforce"]), only_sf)
                self.assertEqual(len(report["manual_review"]["only_on_tenant"]), only_tenant)
                # The counted block itself carries no domain value.
                self.assertNotIn("example", json.dumps(report["domains_check"]))

    def test_an_unreadable_domain_list_fails_closed_before_edit(self):
        (result, report), page = self.run_edit(co0767_row(alternateDomains=None))
        self.assertEqual(result, "renewal_row_schema_unexpected")
        self.assertEqual([s for s in page.selectors if "data-am" in s], [])
        self.assertIsNone(report["domains_check"])

    def test_a_ce_domain_difference_alone_is_not_a_root_domain_mismatch(self):
        # The Leaked Credentials scanned domain is not a root domain of the CO: it keeps its own add-only path.
        self.assertEqual(runner.renewal_domains_check(source(), tenant_row())["result"], "match")
        self.assertNotEqual(CE_DOMAIN, DOMAIN)


class CommandLineDomainsTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, directory, ignore_errors=True)
        self.outcomes = directory / "outcomes.json"
        for patcher in (patch.object(runner, "RENEWAL_OUTCOMES_PATH", self.outcomes),
                        patch.object(runner, "_readback_ids", return_value=(ID, UUID))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, *argv):
        with patch.object(sys, "argv", ["runner", *argv]), patch("builtins.print") as printed:
            runner.main()
        return json.loads(printed.call_args.args[0])

    def test_mismatch_prints_manual_review_on_the_terminal_only(self):
        report = {"engine": "case_6_renew_both", "changes": [], "added_domains": 0, "gate": None,
                  "domains_check": {"result": "mismatch", "only_in_salesforce": 1, "only_on_tenant": 2},
                  "manual_review": {"only_in_salesforce": ["new-root.example"],
                                    "only_on_tenant": ["old-one.example", "old-two.example"]}}
        with patch.object(runner, "run_renewal", return_value=(MISMATCH, report)):
            printed = self.main("--co", "CO-0900", "--renew")
        self.assertEqual(printed["result"], MISMATCH)
        self.assertEqual(printed["leonardo_write"], "not_performed")
        self.assertEqual(printed["manual_review"], {"only_in_salesforce": ["new-root.example"],
                                                    "only_on_tenant": ["old-one.example", "old-two.example"]})
        self.assertNotIn("manual_review", printed["plan"])
        self.assertEqual(printed["plan"]["domains_check"], {"result": "mismatch", "only_in_salesforce": 1,
                                                            "only_on_tenant": 2})
        stored_text = self.outcomes.read_text(encoding="utf-8")
        self.assertNotIn("example", stored_text)  # no domain value reaches attended_renewal_outcomes.json
        stored = json.loads(stored_text)["CO-0900"]
        self.assertEqual((stored["result"], stored["domains_check"], stored["domains_only_in_salesforce"],
                          stored["domains_only_on_tenant"]), (MISMATCH, "mismatch", 1, 2))

    def test_match_prints_no_manual_review_and_stores_the_result_only(self):
        report = {"engine": "case_6_renew_both", "changes": [], "added_domains": 0, "gate": None,
                  "domains_check": {"result": "match", "count": 5}}
        with patch.object(runner, "run_renewal", return_value=("renewal_already_current", report)):
            printed = self.main("--co", "CO-0900", "--renew")
        self.assertNotIn("manual_review", printed)
        stored = json.loads(self.outcomes.read_text(encoding="utf-8"))["CO-0900"]
        self.assertEqual(stored["domains_check"], "match")
        self.assertNotIn("domains_only_in_salesforce", stored)


# --- scan status for renewal COs --------------------------------------------------------------------------------

RENEW_ROW = {"Name": "CO-0767", "Account_Name__c": "A2A", "Onboarding_Product__c": "Surface & Credential Exposure",
             "Onboarding_Type__c": "Renewal of Existing Product"}
DEV_ID, DEV_UUID = "e" * 24, "f" * 32


def mirror(**overrides):
    record = {"mirror_of_production": True, "source_prod_id": "p1", "created_on": "2026-10-06", "status": "verified",
              "environment": "dev", "surface_account_id": DEV_ID, "account_uuid": DEV_UUID}
    record.update(overrides)
    return {"CO-0767": record}


READBACK = {"surface_account_id": DEV_ID, "account_uuid": DEV_UUID}


class RenewalScanStatusNameTests(unittest.TestCase):
    def names(self, records, row=RENEW_ROW, readback=READBACK):
        with patch.object(runner, "_sf_records", return_value=[row]), \
                patch.object(runner, "_mirror_records", return_value=records):
            return runner._scan_status_tenant_names("CO-0767", True, readback)

    def refused(self, code, *args, **kwargs):
        with self.assertRaises(runner.SurfaceSourceError) as caught:
            self.names(*args, **kwargs)
        self.assertEqual(str(caught.exception), code)

    def test_a_verified_mirror_with_the_readback_id_supports_the_renewal(self):
        names = self.names(mirror())
        self.assertEqual(names, (runner.surface_names("A2A").tenant_name, runner.ce_only_names("A2A").tenant_name))

    def test_no_mirror_or_a_mismatch_fails_closed(self):
        code = "scan_status_renewal_mirror_missing"
        self.refused(code, {})
        self.refused(code, mirror(surface_account_id="d" * 24))
        self.refused(code, mirror(account_uuid="9" * 32))
        self.refused(code, mirror(status="create_attempted"))
        self.refused(code, mirror(environment="prod"))
        self.refused(code, {"CO-0999": mirror()["CO-0767"]})
        self.refused(code, mirror(), readback={"surface_account_id": "d" * 24, "account_uuid": DEV_UUID})

    def test_an_unreadable_mirror_store_fails_closed(self):
        with patch.object(runner, "_sf_records", return_value=[RENEW_ROW]), \
                patch.object(runner, "_mirror_records", side_effect=ValueError("mirror_record_unreadable")):
            with self.assertRaises(runner.SurfaceSourceError) as caught:
                runner._scan_status_tenant_names("CO-0767", True, READBACK)
        self.assertEqual(str(caught.exception), "scan_status_renewal_mirror_missing")

    def test_single_product_renewals_and_other_routes_stay_unsupported(self):
        for product, kind in (("Surface", "Renewal of Existing Product"), ("Credential Exposure", "Renewal of Existing Product"),
                              ("Surface", "Something Else")):
            self.refused("scan_status_route_unsupported", mirror(), row=dict(RENEW_ROW, Onboarding_Product__c=product,
                                                                            Onboarding_Type__c=kind))

    def test_case_4_and_5_types_are_supported_with_a_mirror(self):
        for kind in ("Renewal of Surface + New Credential Exposure Module",
                     "Renewal of Credential Exposure Module + New Surface Product"):
            self.assertEqual(len(self.names(mirror(), row=dict(RENEW_ROW, Onboarding_Type__c=kind))), 2)


class RunScanStatusTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, directory, ignore_errors=True)
        self.dir = directory
        (directory / "readbacks.json").write_text(json.dumps({"CO-0767": READBACK}), encoding="utf-8")
        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: contextlib.nullcontext(object())
        package = types.ModuleType("playwright")
        package.sync_api = fake

        @contextlib.contextmanager
        def page(_playwright):
            yield object()

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": fake}))
        stack.enter_context(patch.object(runner, "READBACK_PATH", directory / "readbacks.json"))
        stack.enter_context(patch.object(runner, "SCAN_STATUS_PATH", directory / "scan.json"))
        stack.enter_context(patch.object(runner, "CHECK_STATE_PATH", directory / "checks.json"))
        stack.enter_context(patch.object(runner, "_attended_page", page))
        stack.enter_context(patch.object(runner, "_sf_records", return_value=[RENEW_ROW]))
        stack.enter_context(patch.object(runner, "_mirror_records", return_value=mirror()))
        stack.enter_context(patch.object(runner, "_open_search", side_effect=AssertionError("invisible search only")))
        self.stack = stack

    def recorded(self):
        return json.loads((self.dir / "checks.json").read_text(encoding="utf-8"))["CO-0767"]

    def row(self, name="A2A", **extra):
        return {"id": DEV_ID, "accountUuid": DEV_UUID.upper(), "accountName": name, "lastReconScan": None, **extra}

    def test_a_renewal_with_a_verified_mirror_records_a_scan_status_through_the_hidden_search(self):
        miss = runner.TenantSearchResult([{"id": "c" * 24, "accountUuid": DEV_UUID, "accountName": "Other"}], 1)
        hit = runner.TenantSearchResult([self.row()], 1)
        with patch.object(runner, "_search_tenants", side_effect=[miss, hit]) as search, \
                patch.object(runner, "read_scan_executions", return_value={"executions": [], "execution_state": "no_executions"}):
            self.assertEqual(runner.run_scan_status("CO-0767", surface_only=True), "scan_status_recorded")
        self.assertEqual([call.args[1] for call in search.call_args_list], [None, None])  # no search box handle
        self.assertEqual([call.args[2] for call in search.call_args_list],
                         [runner.surface_names("A2A").tenant_name, runner.ce_only_names("A2A").tenant_name])
        stored = json.loads((self.dir / "scan.json").read_text(encoding="utf-8"))["CO-0767"]
        self.assertEqual(stored["state"], "no_scan")
        entry = self.recorded()
        self.assertEqual((entry["kind"], entry["result"]), ("scan_status", "scan_status_recorded"))
        self.assertRegex(entry["completed_on"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")

    def test_the_id_and_uuid_match_stays_mandatory(self):
        wrong = runner.TenantSearchResult([dict(self.row(), id="c" * 24)], 1)
        with patch.object(runner, "_search_tenants", return_value=wrong):
            self.assertEqual(runner.run_scan_status("CO-0767", surface_only=True), "scan_status_tenant_not_found")
        self.assertEqual(self.recorded()["result"], "scan_status_tenant_not_found")
        twins = runner.TenantSearchResult([self.row(), self.row()], 2)
        with patch.object(runner, "_search_tenants", return_value=twins):
            self.assertEqual(runner.run_scan_status("CO-0767", surface_only=True), "scan_status_tenant_not_found")
        self.assertFalse((self.dir / "scan.json").exists())

    def test_a_search_that_gets_no_reply_fails_closed_and_is_recorded(self):
        with patch.object(runner, "_search_tenants", return_value=None):
            self.assertEqual(runner.run_scan_status("CO-0767", surface_only=True), "scan_status_schema_unavailable")
        self.assertEqual(self.recorded()["result"], "scan_status_schema_unavailable")

    def test_no_mirror_stops_before_the_browser_and_is_recorded(self):
        with patch.object(runner, "_mirror_records", return_value={}), \
                patch.object(runner, "_attended_page", side_effect=AssertionError("no browser")):
            self.assertEqual(runner.run_scan_status("CO-0767", surface_only=True), "scan_status_renewal_mirror_missing")
        self.assertEqual(self.recorded()["result"], "scan_status_renewal_mirror_missing")

    def test_every_failure_code_is_recorded_but_a_skipped_ce_only_co_is_not(self):
        with patch.object(runner, "_run_scan_status", return_value="leonardo_session_expired"):
            runner.run_scan_status("CO-0767")
        self.assertEqual(self.recorded()["result"], "leonardo_session_expired")
        (self.dir / "checks.json").unlink()
        with patch.object(runner, "_run_scan_status", return_value="scan_status_not_applicable"):
            runner.run_scan_status("CO-0767", surface_only=True)
        self.assertFalse((self.dir / "checks.json").exists())

    def test_the_sweep_includes_renewal_cos_with_a_mirror(self):
        with patch.object(runner, "_run_scan_status", return_value="scan_status_recorded") as inner:
            self.assertEqual(runner.run_scan_status_all(), {"CO-0767": "scan_status_recorded"})
        inner.assert_called_once_with("CO-0767", True)  # surface_only keeps the CE-only skip


# --- hidden search for the read-only paths (owner 2026-10-07) -----------------------------------------------------

class HiddenSearchReadOnlyTests(unittest.TestCase):
    def setUp(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, directory, ignore_errors=True)
        fake = types.ModuleType("playwright.sync_api")
        fake.sync_playwright = lambda: contextlib.nullcontext(object())
        package = types.ModuleType("playwright")
        package.sync_api = fake

        @contextlib.contextmanager
        def page(_playwright):
            yield object()

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, {"playwright": package, "playwright.sync_api": fake}))
        stack.enter_context(patch.object(runner, "_attended_page", page))
        stack.enter_context(patch.object(runner, "READBACK_PATH", directory / "readbacks.json"))
        self.open_search = stack.enter_context(patch.object(runner, "_open_search", return_value=None))
        stack.enter_context(patch.object(runner, "_capture_search_diagnostics"))
        self.directory = directory

    def test_readback_does_not_need_the_visible_search_box(self):
        names = runner.ce_only_names("Acme")
        ce_source = runner.CeFillSource(
            reference="CO-0702", source_revision="rev", account_id="a123456789012345", account_name="Acme",
            email_domain="acme.com", country="France", subscription_start=date(2026, 9, 28),
            subscription_end=date(2029, 9, 27), tenant_name=names.tenant_name, primary_user_alias=names.primary_user_alias)
        with patch.object(runner, "ce_fill_source", return_value=ce_source), \
                patch.object(runner, "_search_tenants", return_value=None) as search:
            result = runner.run_readback("CO-0702")
        self.assertEqual(result, "duplicate_schema_unavailable")  # the search itself failed, not the missing box
        self.assertEqual(search.call_args.args[1], None)
        self.open_search.assert_not_called()

    def test_validate_does_not_need_the_visible_search_box(self):
        prepared = (runner.SURFACE_ENGINE, "Acme", (ID, UUID), None, "", None)
        with patch.object(runner, "_validation_inputs", return_value=prepared), \
                patch.object(runner, "_search_tenants", return_value=None) as search:
            self.assertEqual(runner.run_validate("CO-0801"), "validation_schema_unavailable")
        self.assertEqual(search.call_args.args[1], None)
        self.open_search.assert_not_called()

    def test_create_path_duplicate_check_still_requires_the_visible_search_box(self):
        contract = types.SimpleNamespace(load_source=lambda reference: types.SimpleNamespace(tenant_name="Acme"),
                                         redactions=lambda s: (), primary_domain=lambda s: "acme.com")
        log = types.SimpleNamespace(add_redactions=lambda *a: None, event=lambda *a, **k: None,
                                    attach=lambda page: None, error=lambda *a, **k: None)
        with patch.object(runner, "_search_tenants", side_effect=AssertionError("not reached")):
            self.assertEqual(runner._duplicate_check("CO-0702", log, contract), "duplicate_search_schema_unavailable")
        self.open_search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
