"""CO-0800 (Surface & Credential Exposure, New Product Onboarding = Case 3): the production DRY RUN runner, step (b).

``run_production_dry_run(..., route=...)`` selects the route's source and intended values; the create stays CE-only. A Case 3 dry
run: bare tenant name (no suffix), the main domain as primary domain, the single CE email domain as the Leaked Credentials domain,
Weekly/Monthly by tier, subdomain counts from the licence, the run day as licence start and the subscription end as expiry, a duplicate
check over the name AND both domains, and an explicit refusal of alternate domains and subdomains. Confirm is never clicked, the approval
is never consumed. Hermetic: fake page, fake source, temporary folders; no Salesforce, Redash, network or browser.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import production_dry_run as pdr
from integration.onboarding import session_readiness
from integration.tests import test_production_dry_run as dry
import tools.attended_ce_only_playwright as runner

CASE3 = "case_3_combined_baseline"
CE = "case_2_new_ce_only"
CO, REF, REV, NOW = dry.CO, dry.REF, dry.REV, dry.NOW
NAME, MAIN, EMAIL = "Example Klinik gemeinnuetzige GmbH", "example-klinik.example", "example-klinik-mail.example"
RUN_DAY = NOW.date()


def make_case3_source(*, alternates=(), subdomains=(), ce_end=date(2029, 9, 30), **over):
    entitlement = runner.SurfaceEntitlement(tier="Go", scanning_interval="Monthly", baseline_subdomains=500, addon_subdomains=0,
                                            product_domains=None, subscription_start=date(2026, 10, 1),
                                            subscription_end=date(2029, 9, 30), core_plus_present=True)
    surface = runner.SurfaceFillSource(reference=CO, source_revision=REV, account_id="001AAAAAAAAAAAAAAA", account_name=NAME,
                                       country="Chile", main_domain=MAIN, alternate_domains=tuple(alternates),
                                       subdomains=tuple(subdomains), entitlement=entitlement, tenant_name=NAME,
                                       primary_user_alias="ekg", salesforce_id_present=False)
    data = dict(surface=surface, ce_email_domain=EMAIL, ce_subscription_start=date(2026, 10, 1), ce_subscription_end=ce_end)
    data.update(over)
    return runner.Case3FillSource(**data)


class Recorder(dict):
    """Search replies by value; remembers every value that was asked."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.asked = []

    def get(self, key, default=None):
        self.asked.append(key)
        return super().get(key, default)


def hit(**row):
    base = {"name": "Someone Else", "domain": "elsewhere.example", "alternates": [], "emails": [], "deleted": False}
    base.update(row)
    return {"status": 200, "total": 1, "rows": [base]}


MAX_SCAN_LABEL = "Maximum scan Duration (hours)"


class C3Page(dry.FakePage):
    """A fake production page with the Surface-only control. ``max_scan``: present | absent | disabled | stuck (ignores typing).
    ``scan_now_hides``: the "Scan now" control disappears once a Weekly/Monthly interval is chosen."""

    def __init__(self, *, max_scan="present", scan_now_hides=False, **kw):
        super().__init__(**kw)
        self.scan_now_hides = scan_now_hides
        if max_scan != "absent":
            self.texts[MAX_SCAN_LABEL] = dry.Ctl(self, "text", MAX_SCAN_LABEL, value="24", enabled=max_scan != "disabled",
                                                 mangle=(lambda _v: "24") if max_scan == "stuck" else None)

    def get_by_label(self, pattern):
        if (self.scan_now_hides and pattern.search("Scan now")
                and self.selects["scanningInterval"].value in ("Weekly", "Monthly")):
            return dry.Missing()
        return super().get_by_label(pattern)


class C3Harness(dry.Harness):
    def __init__(self, test, *, approval_route=CASE3, max_scan="present", scan_now_hides=False, **page_kw):
        page_kw["search"] = Recorder(page_kw.pop("search", {}))
        super().__init__(test, **page_kw)  # issues a CE-only approval
        self.page = C3Page(max_scan=max_scan, scan_now_hides=scan_now_hides, search=self.page.search)
        if approval_route != CE:
            self.approval_path.unlink()
            approval.issue(self.approval_path, REF, CO, approval_route, REV, "milton", frozenset({"milton"}),
                           NOW.replace(hour=11))
        self.source = make_case3_source()
        self.date_calls = []

    def load(self, _ref):
        self.loaded += 1
        if isinstance(self.source, Exception):
            raise self.source
        return self.source

    def go(self, *, route=CASE3, supported=True, clone=dry.good_clone, environ=None):
        env = dict(dry.UNLOCK if environ is None else environ)

        def fill_date(_page, key, value):
            self.date_calls.append((key, value))
            return True

        with mock.patch.object(session_readiness, "PRODUCTION_DRY_RUN_SUPPORTED", supported):
            line = runner.run_production_dry_run(
                CO, REV, route=route, environ=env, now=NOW, records_parent=self.records, approval_path=self.approval_path,
                load_source=self.load, clone_facts=clone, open_page=self.open_page, fill_date=fill_date,
                date_kept=lambda *_a: True, org_probe=dry.ORG_PROBE)
        return json.loads(line)

    def case3_approval_unused(self):
        return approval.check(self.approval_path, REF, CO, CASE3, REV, NOW)


class PassingRunTests(unittest.TestCase):
    def test_a_case_3_dry_run_fills_and_verifies_every_control_and_cancels(self):
        h = C3Harness(self)
        out = h.go()
        self.assertEqual((out["result"], out["outcome"]), ("dry_run_passed", "dry_run_passed"))
        record, text = h.record()
        self.assertEqual(record["route"], CASE3)
        fields = record["form_fields_checked"]
        self.assertTrue(fields and all(value == "pass" for value in fields.values()), fields)
        self.assertNotIn("Confirm", h.page.clicks)
        self.assertIn("Cancel", h.page.clicks)
        self.assertIsNone(h.case3_approval_unused())  # a dry run never consumes the approval
        for secret in (NAME, MAIN, EMAIL):
            self.assertNotIn(secret, text)  # field names and pass/fail only

    def test_it_types_the_case_3_contract_not_the_ce_only_one(self):
        h = C3Harness(self)
        h.go()
        texts = {label: ctl.value for label, ctl in h.page.texts.items()}
        self.assertEqual(texts["Company name"], NAME)  # bare name, no " - CE Only"
        self.assertEqual(texts["Company primary domain"], MAIN)
        self.assertEqual(texts["Leaked Credentials scanned domains (Comma Separated Values)"], EMAIL)
        self.assertEqual(texts["User email domains (Comma Separated Values)"], "pentera.io")
        self.assertEqual((texts["Number of assets"], texts["Number of domains"], texts["Number of subdomains"]), ("10000", "500", "500"))
        self.assertEqual(h.page.selects["scanningInterval"].value, "Monthly")  # Go tier
        self.assertEqual(h.page.selects["leakedCredentialsScanningInterval"].value, "Weekly")
        self.assertEqual(h.page.advanced_state["subDomainsReconEnabled"], True)
        self.assertEqual(h.page.advanced_state["webDictionaryBruteForceEnabled"], True)
        self.assertEqual(h.page.advanced_state["fullNucleiScanEnabled"], True)
        self.assertEqual(h.page.advanced_state["automatedDiscoveryEnabled"], False)
        for key, want in (("notificationsAllowed", True), ("multipleUsersAllowed", True), ("apiAccessAllowed", True),
                          ("leakedCredentialsAllowed", True), ("phishingEnabled", False), ("provisioningEnabled", True)):
            self.assertIs(h.page.boxes[key].checked, want, key)

    def test_the_licence_starts_on_the_run_day_and_ends_on_the_subscription_end(self):
        h = C3Harness(self)
        h.go()
        self.assertEqual(dict(h.date_calls), {"license_start": RUN_DAY, "license_end": date(2029, 9, 30)})


class DuplicateTests(unittest.TestCase):
    def test_the_name_the_main_domain_and_the_ce_email_domain_are_all_searched(self):
        h = C3Harness(self)
        h.go()
        self.assertTrue({NAME, MAIN, EMAIL} <= set(h.page.search.asked), h.page.search.asked)

    def test_a_match_on_any_of_them_refuses_before_the_form_opens(self):
        for label, search in (("name", {NAME: hit(name=NAME)}), ("main domain", {MAIN: hit(domain=MAIN)}),
                              ("ce email domain", {EMAIL: hit(domain=EMAIL)}),
                              ("ce domain on another tenant's alternates", {EMAIL: hit(alternates=[EMAIL])})):
            with self.subTest(match=label):
                h = C3Harness(self, search=search)
                out = h.go()
                self.assertEqual((out["result"], out["outcome"]), ("duplicate_in_production", "refused"))
                self.assertEqual(h.page.clicks, [])
                self.assertIsNone(h.case3_approval_unused())

    def test_the_default_clone_check_covers_both_domains_for_case_3_and_one_for_ce_only(self):
        from integration.onboarding import leonardo_inventory as inventory
        from integration.onboarding import production_create_live as live

        seen = []
        payload = {"captured_at": "2026-10-16T11:00:00Z", "tenants": []}
        gate = lambda _root, _name, domains, **_kw: seen.append(tuple(domains)) or {"result": "production_clone_no_match"}  # noqa: E731
        with mock.patch.object(inventory, "load_latest", return_value=payload), \
                mock.patch.object(inventory, "production_duplicate_gate", side_effect=gate), \
                mock.patch.object(live, "clone_search_control", return_value={}):
            runner._pdr_clone_facts(make_case3_source(), NOW, CASE3)
            runner._pdr_clone_facts(dry.make_source(), NOW)
        self.assertEqual(seen, [(MAIN, EMAIL), (dry.POISON_DOMAIN,)])


class RefusalTests(unittest.TestCase):
    def test_alternate_domains_and_subdomains_are_refused_explicitly_before_any_browser(self):
        for label, source in (("alternate", make_case3_source(alternates=("alt.example",))),
                              ("subdomain", make_case3_source(subdomains=("app.example-klinik.example",)))):
            with self.subTest(listed=label):
                h = C3Harness(self)
                h.source = source
                out = h.go()
                self.assertEqual((out["result"], out["outcome"]), ("case3_alternate_domains_not_supported", "refused"))
                self.assertEqual((h.opened, h.page.clicks), (0, []))

    def test_any_other_route_is_refused_before_a_source_read_or_a_browser(self):
        for route in ("case_1_new_surface_only", "case_4_renew_surface_new_ce", "renewal_run", "", "bogus"):
            with self.subTest(route=route):
                h = C3Harness(self)
                out = h.go(route=route)
                self.assertEqual((out["result"], out["outcome"]), ("route_not_ce_only_new", "refused"))
                self.assertEqual((h.loaded, h.opened), (0, 0))

    def test_a_ce_only_approval_cannot_run_a_case_3_dry_run(self):
        h = C3Harness(self, approval_route=CE)
        out = h.go()
        self.assertEqual(out["outcome"], "refused")
        self.assertTrue(out["result"].startswith("approval_"), out["result"])
        self.assertEqual((h.opened, h.page.clicks), (0, []))

    def test_the_dry_run_lock_refuses_before_any_read(self):
        h = C3Harness(self)
        out = h.go(supported=False)
        self.assertEqual(out["result"], "production_locked_production_dry_run_not_supported")
        self.assertEqual((h.loaded, h.opened), (0, 0))

    def test_source_problems_are_reported_with_their_own_codes_never_unexpected_error(self):
        codes = ("case3_route_mismatch", "case3_ce_email_domain_invalid", "case3_core_plus_missing", "case3_source_unavailable",
                 "surface_main_domain_invalid", "surface_domains_exceed_license", "surface_networks_not_supported",
                 "surface_baseline_unavailable", "ce_subscription_ambiguous")
        for code in codes:
            with self.subTest(code=code):
                h = C3Harness(self)
                h.source = runner.SurfaceSourceError(code)
                self.assertEqual(h.go()["result"], code)
                self.assertEqual(h.opened, 0)

    def test_a_term_mismatch_between_surface_and_core_plus_stops_before_the_browser(self):
        h = C3Harness(self)
        h.source = make_case3_source(ce_end=date(2028, 9, 30))
        out = h.go()
        self.assertEqual((out["result"], out["outcome"], h.opened), ("case3_term_mismatch", "refused", 0))


class CliTests(unittest.TestCase):
    def run_main(self, *extra):
        argv = ["runner", "--production-dry-run", "--co", CO, "--revision", REV, *extra]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(runner, "run_production_dry_run",
                                                                      return_value="{}") as called:
            try:
                runner.main()
            except SystemExit as exit_:
                return called, exit_.code
        return called, None

    def test_route_defaults_to_ce_only_and_case_3_is_accepted(self):
        called, _ = self.run_main()
        called.assert_called_once_with(CO, REV, route=CE)
        called, _ = self.run_main("--route", CASE3)
        called.assert_called_once_with(CO, REV, route=CASE3)

    def test_any_other_route_is_a_usage_error(self):
        for route in ("case_1_new_surface_only",):
            called, code = self.run_main("--route", route)
            self.assertNotEqual(code, None)
            called.assert_not_called()

    def test_the_live_create_cli_stays_ce_only(self):
        argv = ["runner", "--production-create", "--co", CO, "--revision", REV, "--route", CASE3, "--owner-go"]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(runner, "run_production_create") as called:
            with self.assertRaises(SystemExit):
                runner.main()
        called.assert_not_called()


class ReasonCodeTests(unittest.TestCase):
    def test_the_case_3_codes_are_known_reasons(self):
        for code in ("case3_alternate_domains_not_supported", "case3_term_mismatch", "case3_route_mismatch",
                     "case3_ce_email_domain_invalid", "case3_core_plus_missing", "case3_source_unavailable"):
            self.assertEqual(pdr.known_reason(code), code)
        self.assertEqual(pc.DRY_RUN_ROUTES, (CE, CASE3))


if __name__ == "__main__":
    unittest.main()
