"""The Case 3 "Add Account values" card (path A, owner decision 2026-10-21): the exact values to type in BackOffice for CO-0800, taken
from the SAME plan the production dry run verified (so the card and the dry run can never disagree), plus the manual steps and the HAR
capture steps. Read-only: no form, no button, nothing written. Hermetic: fake plans, no Salesforce, no browser.
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import production_case3_sheet as sheet
from integration.onboarding import production_dry_run as pdr
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run_ui as base
import tools.attended_ce_only_playwright as runner

REV = base.REV


def plan(**source_kw):
    return runner.build_case3_fill(c3.make_case3_source(**source_kw), c3.RUN_DAY)


def by_label(rows):
    return {item["label"]: item for item in rows}


class RowsTests(unittest.TestCase):
    def test_every_value_comes_from_the_plan_in_form_order(self):
        rows = sheet.rows(plan())
        labels = [item["label"] for item in rows]
        for earlier, later in (("Company name", "Company primary domain"), ("Company primary domain", "User email domains"),
                               ("User email domains", "Country"), ("Country", "Scanning interval"),
                               ("Scanning interval", "Leaked Credentials"), ("Leaked Credentials", "Automated discovery"),
                               ("Automated discovery", "Maximum scan Duration (hours)"),
                               ("Maximum scan Duration (hours)", "License Type"), ("License Type", "Start date"),
                               ("Start date", "Expiration date")):
            self.assertLess(labels.index(earlier), labels.index(later), (earlier, later))
        rows = by_label(rows)
        self.assertEqual(rows["Company name"]["value"], c3.NAME)  # no " - CE Only" for Case 3
        self.assertEqual(rows["Company primary domain"]["value"], c3.MAIN)
        self.assertEqual(rows["Leaked Credentials scanned domains"]["value"], c3.EMAIL)
        self.assertEqual(rows["User email domains"]["value"], "pentera.io")
        self.assertEqual((rows["Scanning interval"]["value"], rows["Leaked Credentials scanning interval"]["value"]),
                         ("Monthly", "Weekly"))
        self.assertEqual((rows["Number of assets"]["value"], rows["Number of domains"]["value"],
                          rows["Number of subdomains"]["value"]), ("10000", "10000", "500"))
        self.assertEqual((rows["Start date"]["value"], rows["Expiration date"]["value"]), (c3.RUN_DAY.isoformat(), "2029-09-30"))
        self.assertEqual(rows["Country"]["value"], "Chile")

    def test_the_guru_settings_are_shown_with_the_90_hours_and_the_conditional_static_ip(self):
        rows = by_label(sheet.rows(plan()))
        self.assertEqual(rows["Maximum scan Duration (hours)"]["value"], "90")
        self.assertIn("24", rows["Maximum scan Duration (hours)"]["note"])  # the form starts at 24: change it
        for label, want in (("Automated discovery", "Off"), ("Recon Subdomains", "On"), ("Web dictionary brute force", "On"),
                            ("Web dorking", "Off"), ("Nuclei", "On"), ("Authenticated Testing", "Off"),
                            ("Static outbound IP", "Off"), ("AI", "Off"), ("Multiple attack stacks", "Off"),
                            ("Notifications", "On"), ("Multiple users", "On"), ("API access", "On"), ("Phishing", "Off"),
                            ("Leaked Credentials", "On"), ("Include Provisioning", "On"), ("Include subdomains", "On"),
                            ("MFA required", "On")):
            self.assertEqual(rows[label]["value"], want, label)
        self.assertIn("explicitly", rows["Static outbound IP"]["note"])
        self.assertEqual(rows["License Type"]["value"], "Prepaid annual subscription")

    def test_blank_controls_are_left_empty_and_listed_domains_are_shown_when_present(self):
        rows = by_label(sheet.rows(plan()))
        for label in ("Alternate Domains", "SubDomains", "Networks", "Phone number", "Job title"):
            self.assertEqual((rows[label]["status"], rows[label]["value"][:5]), ("leave", "Leave"), label)
        self.assertEqual((rows["Operator Account"]["status"], rows["Operator Account"]["value"]), ("enter", "The Technical Advisor"))
        listed = by_label(sheet.rows(plan(alternates=("alt.example",), subdomains=("a.b.example",))))
        self.assertEqual((listed["Alternate Domains"]["value"], listed["SubDomains"]["value"]), ("alt.example", "a.b.example"))
        self.assertEqual(listed["Alternate Domains"]["status"], "enter")

    def test_scan_now_is_left_as_the_form_sets_it_and_the_primary_user_is_new_and_internal(self):
        rows = by_label(sheet.rows(plan()))
        self.assertEqual(rows["Scan now"]["value"], "Leave as the form sets it")
        self.assertEqual(rows["Primary user"]["value"], "New User")
        self.assertTrue(rows["Organization Email"]["value"].endswith("@pentera.io"))
        self.assertEqual(rows["SpyCloud"]["value"], "On")

    def test_the_rows_are_exactly_what_the_dry_run_types_and_verifies(self):
        """One source of truth: every text, select and toggle the dry run verifies appears on the card with the same value."""
        full = plan()
        intended = pdr.map_plan(full, runner.SELECT_NAMES, runner.EARLY_SELECTS)
        shown = {" ".join(item["value"].split()) for item in sheet.rows(full)}
        for _label, value in intended.texts:
            self.assertIn(" ".join(value.split()), shown, value)
        for _name, option, _early in intended.selects:
            self.assertIn(option, shown, option)
        self.assertEqual(dict(intended.advanced_texts)["Maximum scan Duration (hours)"], "90")
        self.assertIn("90", shown)

    def test_an_incomplete_plan_fails_closed(self):
        broken = plan()
        del broken["checkboxes"]["fullNucleiScanEnabled"]
        with self.assertRaises(ValueError):
            sheet.rows(broken)
        with self.assertRaises(ValueError):
            sheet.rows({"texts": {}})

    def test_the_steps_cover_the_har_capture_the_ids_the_stage_and_the_next_day_check(self):
        text = " ".join(sheet.after_steps(c3.NAME, "CO-0800"))
        for word in ("Network", "Preserve log", "HAR", "Confirm", "Surface_Account_ID__c", "Account_UUID__c", "Account Scanning",
                     "next day", "User Created"):
            self.assertIn(word, text)
        self.assertIn(c3.NAME, text)
        self.assertNotIn("guarded one-field write", text)  # that writer exists only for CE-only


class CardTests(unittest.TestCase):
    def row(self, **over):
        return base.row(**over)

    def card(self, row=None, plan_result=None, error=None):
        values = mock.Mock(side_effect=error) if error else mock.Mock(return_value=(REV, plan_result or plan()))
        with mock.patch.object(dashboard, "case3_values", values):
            return dashboard._case3_values_card("CO-0800", row if row is not None else self.row())

    def test_a_ready_co_shows_the_values_steps_and_no_control(self):
        html = self.card()
        for word in ("Add Account values", c3.NAME, c3.MAIN, "Monthly", "Maximum scan Duration (hours)", "90", "HAR",
                     "Surface_Account_ID__c"):
            self.assertIn(word, html)
        for tag in ("<form", "<button", "<input", "<script", "method='post'"):
            self.assertNotIn(tag, html)

    def test_values_are_escaped(self):
        evil = plan()
        evil["texts"]["Company name"] = "<script>alert(1)</script> & Co"
        html = self.card(plan_result=evil)
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)

    def test_hard_blockers_show_do_not_onboard_and_no_values(self):
        for over, word in ((dict(Onboarding_Approval_Status__c="Pending"), "not Approved"),
                           (dict(Account_UUID__c="a" * 32), "Account UUID"),
                           (dict(Surface_Account_ID__c="b" * 24), "Surface Account ID")):
            html = self.card(row=self.row(**over))
            self.assertIn("Do not onboard", html, over)
            self.assertIn(word, html, over)
            self.assertNotIn(c3.MAIN, html)

    def test_a_plan_error_shows_its_code_and_no_values(self):
        for error, code in ((ValueError("case3_term_mismatch"), "case3_term_mismatch"),
                            (runner.SurfaceSourceError("surface_main_domain_invalid"), "surface_main_domain_invalid"),
                            (RuntimeError("secret detail with spaces"), "values_unavailable")):
            html = self.card(error=error)
            self.assertIn("Do not onboard", html)
            self.assertIn(code, html)
            self.assertNotIn("secret detail", html)

    def test_other_routes_and_missing_rows_get_nothing_from_this_card(self):
        self.assertEqual(self.card(row=self.row(Onboarding_Product__c="Credential Exposure")), "")
        self.assertEqual(dashboard._case3_values_card("CO-0800", None), "")

    def test_the_co_page_shows_a_neutral_case_3_message_not_the_red_ce_only_banner(self):
        ce_guide = {"state": "not_applicable", "verdict": "blocked", "checks": [], "rows": [], "leave": "", "after": [],
                    "closing": "x", "as_of": "", "recorded_duplicates": None,
                    "headline": "Not applicable: this CO is not a CE-only New Product Onboarding."}
        guide = dashboard._case3_guide(ce_guide, "CO-0800")
        self.assertEqual(guide["verdict"], "info")
        self.assertNotIn("Not applicable", guide["headline"])
        self.assertIn("Case 3", guide["headline"])
        html = dashboard._production_guide_html(guide, {"status": "not_in_production", "reason": ""})
        self.assertIn("<div class='banner '", html)  # the plain (blue, neutral) banner: no banner-bad / banner-warn
        self.assertNotIn("banner-bad", html)
        self.assertNotIn("banner-warn", html)
        self.assertIn("/onboard/CO-0800", html)
        # any other state of a non-Case 3 guide is untouched
        self.assertIs(dashboard._case3_guide(ce_guide, "CO-0800", case3=False), ce_guide)

    def test_the_review_page_uses_this_card_for_case_3_and_the_ce_sheet_otherwise(self):
        with mock.patch.object(dashboard, "_case3_values_card", return_value="CASE3-CARD") as case3, \
                mock.patch.object(dashboard, "_onboarding_sheet_card", return_value="CE-CARD") as ce:
            self.assertEqual(dashboard._add_account_values_card("CO-0800", self.row(), dashboard.CASE3_ENGINE, match=None,
                                                                 start_iso="", country=None), "CASE3-CARD")
            self.assertEqual(dashboard._add_account_values_card("CO-0798", self.row(), dashboard.CE_ENGINE, match=None,
                                                                 start_iso="", country=None), "CE-CARD")
        case3.assert_called_once()
        ce.assert_called_once()


if __name__ == "__main__":
    unittest.main()
