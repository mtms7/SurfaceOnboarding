"""The simplified production CO page (``/co/CO-x?env=prod``, docs/50): one verdict, a short strip, one flat table.

Hermetic: no Salesforce, BackOffice, VM or network. The page is guidance only: no form, button, input or script, no Dev
text, and every gate is shown once.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
import unittest
from unittest.mock import patch

from integration.onboarding import production_views as views
from integration.tests import test_production_first_dashboard as base
import tools.serve_attended_open_onboardings_dashboard as dashboard
unlocked_build = base.unlocked_build

REF = "CO-0798"
COMMENTS = "DealHub subscription 2026-10-18 - 2027-10-17 approved"
ROW = {"Name": REF, "Account_Name__c": "Advanced Technology International", "Account_UUID__c": None,
       "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT, "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE,
       "Onboarding_Approval_Status__c": "Approved", "Email_Domains__c": "@ati.example", "Onboarding_Comments__c": COMMENTS}
STARTS = {REF: "2026-10-18"}
FIELD_ROWS = ("Company name", "Account Type", "Company primary domain", "User email domains", "Country", "Primary user", "First name",
              "Last name", "Organization Email", "Leaked Credentials", "Leaked Credentials scanning interval",
              "Leaked Credentials scanned domains", "Include Provisioning", "Include subdomains", "License Type", "Number of assets",
              "Number of domains", "Number of subdomains", "Start date", "Expiration date", "SpyCloud")
DUPLICATE = base.match("exists_matches", reason="Same name and domain",
                       candidates=[{**base.LIVE, "name": "Advanced Technology International - CE Only", "domain": "ati.example"}])
TABLE_HEAD = "<table class='dense'><thead><tr><th scope='col'>#</th>"


class PageBase(unittest.TestCase):
    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)
        self.checks = {}

    def page(self, row=ROW, match=None, vm=False, country="United States", starts=STARTS):
        with base.patched(self.env, unavailable=vm, starts=starts), patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_check_state", side_effect=lambda: self.checks), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            return dashboard.page_detail_production(REF, row, match or base.match(), country=country)

    @staticmethod
    def main(page):
        return page[page.index("<main"):]


class LayoutTests(PageBase):
    @unlocked_build
    def test_sections_in_order_and_ready_verdict(self):
        main = self.main(self.page())
        marks = ["<h1>" + REF, "Advanced Technology International", "CE-only", "Ready to onboard", "Before you start",
                 "Enter in BackOffice &gt; Add Account", "After you click Confirm",
                 dashboard.PROD_VIEW_UNLOCKED_CLOSING, "data age"]   # unlocked build: the closing follows the create gate
        positions = [main.index(mark) for mark in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(main.count("class='banner"), 1)   # exactly one verdict banner

    def test_no_dev_tab_section_or_chip(self):
        main = self.main(self.page())
        for text in ("Dev - ", "Not onboarded", "Dev mirror", "Leonardo", "env=dev", "class='seg'", "Review &amp; start (Dev)"):
            self.assertNotIn(text, main)

    def test_no_repeated_gate_cards(self):
        main = self.main(self.page())
        for text in ("Before migration", "Production match</h2>", "Onboard</h2>", "Production onboarding sheet", "Runner",
                     "Production session", "Dry run", "Owner go", "Desktop address", "Clone unavailable"):
            self.assertNotIn(text, main, text)
        self.assertEqual(main.count("Duplicate check"), 1)
        self.assertEqual(main.count("Account UUID empty"), 1)

    def test_strip_has_at_most_five_one_line_checks(self):
        main = self.main(self.page())
        strip = main[main.index("Before you start"):main.index("Enter in BackOffice")]
        labels = re.findall(r"<span class='what'>([^<]+)", strip)
        self.assertEqual(labels, ["Route", "Approved in Salesforce", "One email domain", "Account UUID empty", "Duplicate check"])

    def test_no_form_button_input_or_script_and_no_create_control(self):
        for vm in (False, True):
            page = self.page(vm=vm)
            self.assertEqual(base.controls(page), [])
            main = self.main(page).lower()
            for tag in ("<form", "<button", "<input", "<script", "<select", "<textarea", "onclick", "method='post'"):
                self.assertNotIn(tag, main, tag)
            self.assertNotRegex(main, r"<(button|a)[^>]*>[^<]*(create|confirm|submit)")

    def test_flags_stay_false_and_unlock_adds_no_control(self):
        self.assertIs(dashboard.readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        with patch.object(dashboard.readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            self.assertEqual(base.controls(self.page()), [])


class TableTests(PageBase):
    def test_one_flat_table_with_only_the_fields_to_enter(self):
        main = self.main(self.page())
        self.assertEqual(main.count("<table"), 1)
        self.assertNotIn("tbl-wrap", main)   # no inner scrolling box
        table = main[main.index("<table"):main.index("</table>")]
        labels = [base.text(cell) for cell in re.findall(r"<tr[^>]*><td class='num'>\d+</td><td>(.*?)</td><td><code>", table, flags=re.S)]
        self.assertEqual(labels, list(FIELD_ROWS))
        for dropped in ("Alternate Domains", "SubDomains", "Networks", "MFA required", "Operator Account", "Scanning interval",
                        "Scan now", "Notifications", "Multiple users", "API access", "Phishing", "Advanced switches", "Leave empty"):
            self.assertNotIn(dropped + "</td>", table)
        for text in ("Alternate Domains, SubDomains and Networks empty", "Operator Account None", "Scanning interval None",
                     "MFA required on", "Scan now, Notifications, Multiple users, API access and Phishing off",
                     "every Advanced switch off"):
            self.assertIn(text, main)

    def test_expiry_and_country_rows_are_present_and_highlighted(self):
        table = self.main(self.page())
        table = table[table.index("<table"):table.index("</table>")]
        self.assertRegex(table, r"<tr class='key'><td class='num'>\d+</td><td>Expiration date</td><td><code>2027-10-17</code>")
        self.assertRegex(table, r"<tr class='key'><td class='num'>\d+</td><td>Country</td><td><code>United States</code>")
        self.assertIn("<code>Advanced Technology International - CE Only</code>", table)
        self.assertIn("<code>ati.example</code>", table)

    def test_vm_shows_one_unavailable_country_row_and_no_wall_of_unavailable(self):
        vm_row = {field: None for field in dashboard.DETAIL_FIELDS}
        vm_row.update(ROW)
        with patch.object(dashboard, "_snapshot_as_of", return_value=("2026-10-16 09:50 UTC", False)):
            page = self.page(row=vm_row, vm=True, country=None)
        main = self.main(page)
        self.assertEqual(main.count("<code>unavailable on this server</code>"), 1)
        self.assertEqual(main.count("Unavailable</span>"), 0)
        self.assertNotIn("Ready to onboard", main)   # a key row that cannot be read never reads as ready
        self.assertIn("Blocked: Country unavailable", main)
        self.assertIn("Salesforce data (approval, UUID, domains) was published at 2026-10-16 09:50 UTC", main)
        self.assertIn("<code>2027-10-17</code>", main)
        self.assertEqual(base.controls(page), [])

    def test_hostile_values_are_escaped(self):
        row = {**ROW, "Account_Name__c": "<b>Evil</b> Co"}
        page = self.page(row=row, country="<i>X</i>")
        self.assertNotIn("<b>Evil", page)
        self.assertNotIn("<i>X", page)
        self.assertIn("&lt;b&gt;Evil&lt;/b&gt; Co - CE Only", page)

    def test_after_steps_are_numbered_one_liners(self):
        main = self.main(self.page())
        steps = main[main.index("After you click Confirm"):]
        steps = steps[steps.index("<ol>"):steps.index("</ol>")]
        self.assertEqual(len(re.findall(r"<li>", steps)), 5)
        self.assertIn("Click Confirm in BackOffice yourself.", steps)
        self.assertIn("Account_UUID__c", steps)


class VerdictTests(PageBase):
    def banner(self, page):
        return base.text(re.search(r"<div class='banner[^']*'[^>]*>(.*?)</div>", self.main(page), flags=re.S).group(1))

    def test_stale_clone_is_blocked_with_the_check_backoffice_sentence(self):
        self.env.now = base.CAPTURED + timedelta(hours=7)
        page = self.page(match=base.match("clone_unavailable", reason="inventory_snapshot_stale", age_seconds=None, captured_at=None))
        self.assertIn("banner-bad", page)
        text = self.banner(page)
        self.assertTrue(text.startswith("Blocked: "))
        self.assertIn("check BackOffice yourself for a duplicate before creating", text)
        self.assertNotIn("Ready to onboard", page)
        self.assertIn("Enter in BackOffice", page)   # the values stay visible under the banner

    def test_missing_clone_is_never_a_clearance(self):
        gone = base.Env(clone=False)
        self.addCleanup(gone.tmp.cleanup)
        self.env = gone
        page = self.page(match=base.match("clone_unavailable", reason="inventory_snapshot_missing", age_seconds=None, captured_at=None))
        self.assertIn("Blocked: ", self.banner(page))
        self.assertNotIn("Ready to onboard", page)

    def test_duplicate_blocks_and_shows_the_match_details(self):
        page = self.page(match=DUPLICATE)
        self.assertTrue(self.banner(page).startswith("Blocked: "))
        self.assertIn(views.SHEET_REFUSALS["duplicate_in_production"], self.banner(page))
        main = self.main(page)
        self.assertIn("Production match details", main)
        self.assertIn("Matched production tenants", main)
        self.assertIn("Advanced Technology International - CE Only", main)
        self.assertIn("Live, paid", main)
        self.assertNotIn(TABLE_HEAD, main)   # no values to type for a duplicate
        self.assertNotIn("After you click Confirm", main)

    def test_ambiguous_match_blocks(self):
        page = self.page(match=base.match("ambiguous", candidates=[base.LIVE, {**base.LIVE, "id": "t2"}]))
        self.assertIn(views.SHEET_REFUSALS["live_paid_in_production"], self.banner(page))
        self.assertIn("Matched production tenants", page)

    def test_hard_blockers_refuse_with_one_reason(self):
        for over, code in (({"Account_UUID__c": "a" * 32}, "account_uuid_set"),
                           ({"Email_Domains__c": "@a.example, @b.example"}, "one_email_domain_required"),
                           ({"Onboarding_Approval_Status__c": "Pending"}, "approval_not_approved"),
                           ({"Onboarding_Comments__c": ""}, "dealhub_range_missing")):
            page = self.page(row={**ROW, **over})
            self.assertEqual(self.banner(page), "Blocked: " + views.SHEET_REFUSALS[code], code)
            self.assertNotIn(TABLE_HEAD, page)
            self.assertNotIn("a" * 32, page)

    def test_stale_snapshot_and_closed_window_block(self):
        with patch.object(dashboard, "_snapshot_as_of", return_value=("2026-10-15 09:50 UTC", True)):
            page = self.page(vm=True)
        self.assertIn("Blocked: the Salesforce snapshot is older than 6 h", self.banner(page))
        page = self.page(starts={REF: "2026-12-01"})
        self.assertIn("Blocked: Opens", self.banner(page))

    def test_unavailable_country_blocks_on_the_pc_too(self):
        page = self.page(country=None)
        self.assertTrue(self.banner(page).startswith("Blocked: Country unavailable"))
        self.assertNotIn("Ready to onboard", page)
        self.assertIn("<code>unavailable on this server</code>", page)

    def test_recorded_production_duplicate_blocks_even_with_a_fresh_clear_clone(self):
        self.checks = {REF: {"kind": "production_duplicate", "result": "duplicate_production_clone_match",
                             "detail": '{"matches": [{"name": "Acme", "id": "t9", "created": 1759622400000}]}'}}
        page = self.page(match=base.match("not_in_production"))
        self.assertTrue(self.banner(page).startswith("Blocked: a duplicate in production was recorded"))
        self.assertNotIn("Ready to onboard", page)
        self.assertIn("Already in production", page)
        self.assertIn("<code>t9</code>", page)
        self.checks = {}
        self.assertIn("Ready to onboard", self.page(match=base.match("not_in_production")))

    def test_not_ce_only_is_not_applicable_but_keeps_one_match_line(self):
        other = {**ROW, "Onboarding_Product__c": "Surface", "Onboarding_Type__c": "New Product Onboarding"}
        page = self.page(row=other)
        self.assertIn("Not applicable", self.banner(page))
        self.assertIn("Production match:", page)
        self.assertNotIn("Enter in BackOffice", page)

    def test_guide_failure_fails_closed(self):
        with patch.object(views, "onboarding_sheet", side_effect=RuntimeError("boom")):
            page = self.page()
        self.assertIn("Blocked: the guide is unavailable on this server.", self.banner(page))
        self.assertNotIn(TABLE_HEAD, page)


class HandlerTests(PageBase):
    def test_the_handler_serves_the_page(self):
        from integration.tests import test_dev_prod_areas as areas_module
        with base.patched(self.env, starts=STARTS), patch.object(dashboard, "detail_row", return_value=dict(ROW)), \
                patch.object(dashboard, "_match_of", return_value=base.match()), \
                patch.object(dashboard, "_read_sheet_country", return_value="United States"), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            prod = areas_module.get("/co/" + REF + "?env=prod").pages[0]
        self.assertEqual(prod[0], 200)
        self.assertIn("Ready to onboard", prod[1])
        self.assertIn("envbar prod", prod[1])


class GuideModelTests(unittest.TestCase):
    def test_leave_rows_collapse_and_verdict_priority(self):
        rules = dashboard._sheet_rules()
        co = {"ce_route": True, "approval_status": "Approved", "account_name": "State Land Service", "email_domains": "@vzd.example",
              "uuid_state": "empty", "country": "Chile", "comments": COMMENTS, "start_iso": "2026-10-18"}
        fresh = {"state": "fresh", "age_seconds": 7800, "reason": ""}
        stale = {"state": "stale", "age_seconds": 30000, "reason": ""}
        clear = {"status": "not_in_production", "candidates": [], "tenants_checked": 5}

        def guide(clone, vm=False, stale_snapshot=False):
            sheet = views.onboarding_sheet(co, rules=rules, today=date(2026, 10, 16), days_before=2, vm=vm, clone=clone,
                                           match=clear, as_of="2026-10-16 09:50 UTC" if vm else None, stale=stale_snapshot)
            return views.production_co_guide(sheet, clone=clone)
        ready = guide(fresh)
        self.assertEqual((ready["verdict"], ready["headline"]), ("ready", "Ready to onboard"))
        self.assertTrue(all(item["status"] != "leave" for item in ready["rows"]))
        self.assertEqual(len(ready["checks"]), 5)
        self.assertEqual(len(ready["after"]), 5)
        blocked = guide(stale, vm=True, stale_snapshot=True)
        self.assertEqual(blocked["verdict"], "blocked")
        self.assertIn("clone is 8 h 20 min old", blocked["headline"])   # the clone outranks the old snapshot
        self.assertEqual(guide(None)["verdict"], "blocked")

    def test_a_missing_clone_or_timing_item_is_blocked_not_ready(self):
        rules = dashboard._sheet_rules()
        co = {"ce_route": True, "approval_status": "Approved", "account_name": "State Land Service", "email_domains": "@vzd.example",
              "uuid_state": "empty", "country": "Chile", "comments": COMMENTS, "start_iso": "2026-10-18"}
        fresh = {"state": "fresh", "age_seconds": 7800, "reason": ""}
        clear = {"status": "not_in_production", "candidates": [], "tenants_checked": 5}
        sheet = views.onboarding_sheet(co, rules=rules, today=date(2026, 10, 16), days_before=2, vm=False, clone=fresh, match=clear)
        self.assertEqual(views.production_co_guide(sheet, clone=fresh)["verdict"], "ready")
        for key in ("clone", "timing"):
            cut = {**sheet, "eligibility": [item for item in sheet["eligibility"] if item["key"] != key]}
            got = views.production_co_guide(cut, clone=fresh)
            self.assertEqual(got["verdict"], "blocked", key)
            self.assertTrue(got["headline"].startswith("Blocked: "), key)
        self.assertEqual(views.production_co_guide({**sheet, "eligibility": []}, clone=fresh)["verdict"], "blocked")


if __name__ == "__main__":
    unittest.main()
