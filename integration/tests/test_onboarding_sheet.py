"""The read-only "Production onboarding sheet" (assisted manual CE-only create in BackOffice).

Hermetic: no Salesforce, BackOffice, VM or network. The pure derivation (``production_views.onboarding_sheet``) and the card
on the review page (``/onboard/CO-x``) are tested; the card is guidance only: no
form, button, link or script, and no route writes anything.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from integration.onboarding import production_views as views
from integration.tests import test_production_first_dashboard as base
import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard

TODAY = date(2026, 10, 16)
FRESH = {"state": "fresh", "age_seconds": 7800, "reason": ""}
CLEAR = {"status": "not_in_production", "candidates": [], "tenants_checked": 5}
COMMENTS = "DealHub subscription 2026-10-18 - 2027-10-17 approved"
RULES = dashboard._sheet_rules()
UNAVAILABLE = "unavailable on this server"

LABELS = [
    "Company name", "Account Type", "Company primary domain", "Alternate Domains", "SubDomains", "User email domains", "Networks",
    "Country", "Primary user", "First name", "Last name", "Organization Email", "MFA required", "Operator Account",
    "Scanning interval", "Scan now", "Notifications", "Multiple users", "API access", "Phishing", "Leaked Credentials",
    "Leaked Credentials scanning interval", "Leaked Credentials scanned domains", "Include Provisioning", "Include subdomains",
    "Advanced switches", "License Type", "Number of assets", "Number of domains", "Number of subdomains", "Start date",
    "Expiration date", "SpyCloud"]


def co(**over):
    data = {"ce_route": True, "approval_status": "Approved", "account_name": "State Land Service",
            "email_domains": "@vzd.example", "uuid_state": "empty", "country": "Chile", "comments": COMMENTS,
            "start_iso": "2026-10-18"}
    data.update(over)
    return data


def sheet(vm=False, clone=FRESH, match=CLEAR, **over):
    return views.onboarding_sheet(co(**over), rules=RULES, today=TODAY, days_before=2, vm=vm, clone=clone, match=match)


def rows_of(result):
    return {item["label"]: item for item in result["rows"]}


class SheetRowTests(unittest.TestCase):
    def test_rows_in_the_form_order(self):
        result = sheet()
        self.assertEqual(result["state"], "ready")
        self.assertEqual([item["label"] for item in result["rows"]], LABELS)
        self.assertTrue(all(set(item) == {"label", "value", "note", "status"} for item in result["rows"]))

    def test_every_value(self):
        got = {label: item["value"] for label, item in rows_of(sheet()).items()}
        self.assertEqual(got, {
            "Company name": "State Land Service - CE Only", "Account Type": "Customer", "Company primary domain": "vzd.example",
            "Alternate Domains": "Leave empty", "SubDomains": "Leave empty", "User email domains": "pentera.io",
            "Networks": "Leave empty", "Country": "Chile", "Primary user": "New User", "First name": "Milton",
            "Last name": "Stevenson", "Organization Email": "milton.stevenson+sls@pentera.io",
            "MFA required": "On", "Operator Account": "None", "Scanning interval": "None", "Scan now": "Off",
            "Notifications": "Off", "Multiple users": "Off", "API access": "Off", "Phishing": "Off", "Leaked Credentials": "On",
            "Leaked Credentials scanning interval": "Weekly", "Leaked Credentials scanned domains": "vzd.example",
            "Include Provisioning": "On", "Include subdomains": "On", "Advanced switches": "Off",
            "License Type": "Prepaid annual subscription", "Number of assets": "1", "Number of domains": "1",
            "Number of subdomains": "1", "Start date": "2026-10-16", "Expiration date": "2027-10-17", "SpyCloud": "On"})
        self.assertEqual(sheet()["tenant_name"], "State Land Service - CE Only")

    def test_alias_follows_the_dev_naming_rule(self):
        long_name = sheet(account_name="Grupo Patio Operations International")
        self.assertEqual(rows_of(long_name)["Organization Email"]["value"], "milton.stevenson+gpoi@pentera.io")
        self.assertEqual(rows_of(sheet(account_name="Tango Group Ltd."))["Company name"]["value"], "Tango Group Ltd - CE Only")

    def test_suffix_is_exactly_ce_only(self):
        self.assertTrue(rows_of(sheet())["Company name"]["value"].endswith(" - CE Only"))

    # (range start, range end, expiry): owner decision 2026-10-19, ce_license_dates: the expiry is the range end
    EXPIRY_TABLE = (
        ("2026-10-18", "2027-10-17", "2027-10-17"),   # one-year deal
        ("2026-06-22", "2029-06-21", "2029-06-21"),   # three-year deal (CO-0798): the range end, not one year
        ("2026-10-18", "2027-03-31", "2027-03-31"),   # shorter than a year
        ("2028-02-29", "2031-02-28", "2031-02-28"),   # start on Feb 29
        ("2026-10-16", "2027-10-16", "2027-10-16"),   # a full year: the range end as given
    )

    def test_expiry_follows_the_runner_rule_over_a_table(self):
        for start, end, expected in self.EXPIRY_TABLE:
            comments = "DealHub " + start + " - " + end
            got = rows_of(sheet(comments=comments))["Expiration date"]
            runner_expiry = runner.ce_license_dates(date.fromisoformat(start), date.fromisoformat(end))[1]
            self.assertEqual((got["value"], got["status"]), (expected, "enter"), (start, end))
            self.assertEqual(got["value"], runner_expiry.isoformat(), (start, end))   # pinned to the runner function itself
            self.assertIn(start, got["note"])
            self.assertIn(end, got["note"])

    def test_the_start_date_row_stays_today(self):
        got = rows_of(sheet(comments="DealHub 2026-06-22 - 2029-06-21"))["Start date"]
        self.assertEqual((got["value"], got["status"]), (TODAY.isoformat(), "enter"))
        self.assertIn("The day you enter it", got["note"])

    def test_unusable_dealhub_ranges_refuse_the_sheet(self):
        cases = {
            "dealhub_range_invalid": ("2027-10-17 - 2026-10-18", "2026-10-18 - 2026-10-18"),            # end not after start
            "dealhub_range_ambiguous": ("2026-10-18 - 2027-10-17 and 2026-11-01 - 2027-11-01",),       # two ranges
            "dealhub_range_missing": (None, "", "no dates here", "2026-10-18 -", "2026-10-18 - 2027-1-17"),  # no end / no range
            "dealhub_range_ended": ("2025-01-01 - 2026-01-01", "2025-06-01 - 2026-10-16"),              # expiry not after today
        }
        for reason, comments_list in cases.items():
            for comments in comments_list:
                result = sheet(comments=comments)
                self.assertEqual((result["state"], result["reason"], result["rows"]), ("refused", reason, []), comments)
                self.assertIn(reason, views.SHEET_REFUSALS)

    def test_missing_country_is_unavailable_for_that_row_only(self):
        for country in (None, "", "   "):
            result = sheet(country=country)
            self.assertEqual(result["state"], "ready")
            got = rows_of(result)["Country"]
            self.assertEqual((got["value"], got["status"]), (UNAVAILABLE, "unavailable"))
            self.assertEqual(rows_of(result)["Company name"]["status"], "enter")
        self.assertIn("snapshot", rows_of(sheet(vm=True, country=None))["Country"]["note"])

    def test_row_statuses(self):
        got = rows_of(sheet())
        for label in ("Alternate Domains", "SubDomains", "Networks", "Operator Account", "Scanning interval", "Scan now",
                      "Notifications", "Multiple users", "API access", "Phishing", "Advanced switches"):
            self.assertEqual(got[label]["status"], "leave", label)
        for label in ("Company name", "Country", "Leaked Credentials", "Expiration date", "SpyCloud"):
            self.assertEqual(got[label]["status"], "enter", label)

    def test_primary_user_rows_are_the_new_user_fields(self):
        got = rows_of(sheet())
        self.assertEqual((got["Primary user"]["value"], got["Primary user"]["note"]),
                         ("New User", "Pick New User, not an existing user."))
        self.assertEqual(got["Organization Email"]["note"], "Internal address, never a customer address.")
        labels = [item["label"] for item in sheet()["rows"]]
        self.assertEqual(labels[labels.index("Primary user"):labels.index("Primary user") + 4],
                         ["Primary user", "First name", "Last name", "Organization Email"])

    def test_spycloud_note_says_verify_after(self):
        self.assertIn("verify", rows_of(sheet())["SpyCloud"]["note"])

    def test_no_row_is_ever_a_customer_email(self):
        text = " | ".join(item["value"] + " " + item["note"] for item in sheet()["rows"])
        self.assertNotIn("@vzd.example", text)
        self.assertEqual(re.findall(r"[\w.+-]+@[\w.-]+", text), ["milton.stevenson+sls@pentera.io"])

    def test_after_steps_and_closing_line(self):
        result = sheet()
        steps = " ".join(result["after"])
        for phrase in ("Confirm", "exact name: State Land Service - CE Only", "Edit once", "Advanced switches", "32 hex",
                       "Account_UUID__c", "Onboarding Stage"):
            self.assertIn(phrase, steps)
        self.assertEqual(result["closing"], "Production create stays locked in the dashboard: this sheet is guidance only.")
        self.assertIn("A missing match is never a clearance", result["reminder"])
        self.assertIn("demo domain", result["reminder"])


class SheetRefusalTests(unittest.TestCase):
    def refused(self, reason, **over):
        result = sheet(**over)
        self.assertEqual((result["state"], result["reason"], result["rows"]), ("refused", reason, []))
        self.assertTrue(result["eligibility"])  # the chips still say why
        self.assertIn(reason, views.SHEET_REFUSALS)

    def test_two_domains(self):
        self.refused("one_email_domain_required", email_domains="@a.example, @b.example")
        self.assertEqual(dict((i["label"], i["status"]) for i in sheet(email_domains="a.example b.example")["eligibility"])["Email domain"],
                         "blocked")

    def test_no_domain(self):
        self.refused("one_email_domain_required", email_domains="")
        self.refused("one_email_domain_required", email_domains=None)

    def test_invalid_domain(self):
        self.refused("email_domain_invalid", email_domains="bad_domain")

    def test_uuid_already_present(self):
        self.refused("account_uuid_set", uuid_state="set")

    def test_uuid_unread(self):
        self.refused("account_uuid_unread", uuid_state="unread")

    def test_account_name_unavailable(self):
        for name in (None, "", "   ", "...", "!!!"):
            self.refused("account_name_unavailable", account_name=name)

    def test_not_applicable_when_not_ce_only_new(self):
        result = sheet(ce_route=False)
        self.assertEqual((result["state"], result["rows"], result["eligibility"]), ("not_applicable", [], []))


class EligibilityTests(unittest.TestCase):
    def chips(self, **kwargs):
        return {item["label"]: item for item in sheet(**kwargs)["eligibility"]}

    def test_all_clear(self):
        got = self.chips()
        self.assertEqual(list(got), ["Route", "Salesforce approval", "Email domain", "Account UUID",
                                     "Clone and production match", "Timing window"])
        self.assertTrue(all(item["status"] == "ok" for item in got.values()))
        self.assertIn("Not in production", got["Clone and production match"]["detail"])
        self.assertIn("6 h", got["Clone and production match"]["detail"])

    def test_salesforce_approval(self):
        self.assertEqual(self.chips(approval_status="Pending")["Salesforce approval"]["status"], "blocked")
        self.assertEqual(self.chips(approval_status=None)["Salesforce approval"]["status"], "unavailable")

    def test_clone_and_match_never_green_without_both(self):
        label = "Clone and production match"
        self.assertEqual(self.chips(clone={"state": "stale", "age_seconds": 30000, "reason": ""})[label]["status"], "blocked")
        self.assertEqual(self.chips(clone=None)[label]["status"], "unavailable")
        self.assertEqual(self.chips(match=None)[label]["status"], "unavailable")
        self.assertEqual(self.chips(match={"status": "clone_unavailable", "candidates": []})[label]["status"], "unavailable")
        dup = {"status": "exists_matches", "candidates": [{"name": "A", "live_paid": False}], "tenants_checked": 5}
        self.assertEqual(self.chips(match=dup)[label]["status"], "blocked")

    def test_timing(self):
        self.assertEqual(self.chips(start_iso="2026-11-20")["Timing window"]["status"], "blocked")
        self.assertEqual(self.chips(start_iso="")["Timing window"]["status"], "unavailable")

    DUP = {"status": "exists_matches", "candidates": [{"name": "A", "live_paid": False}], "tenants_checked": 5}
    OTHER = {"status": "exists_other", "candidates": [{"name": "A", "live_paid": False}], "tenants_checked": 5}
    AMBIGUOUS = {"status": "ambiguous", "candidates": [], "tenants_checked": 5}
    TWO_LIVE = {"status": "exists_matches", "candidates": [{"name": "A", "live_paid": True}, {"name": "B", "live_paid": True}],
                "tenants_checked": 5}

    def test_a_hard_blocker_replaces_the_rows_with_do_not_onboard(self):
        cases = (("approval_not_approved", dict(approval_status="Pending")),
                 ("approval_not_approved", dict(approval_status=None)),
                 ("account_uuid_set", dict(uuid_state="set")),
                 ("one_email_domain_required", dict(email_domains="@a.example @b.example")),
                 ("duplicate_in_production", dict(match=self.DUP)),
                 ("duplicate_in_production", dict(match=self.OTHER)),
                 ("live_paid_in_production", dict(match=self.TWO_LIVE)),
                 ("duplicate_in_production", dict(match=self.AMBIGUOUS)))
        for reason, over in cases:
            result = sheet(**over)
            self.assertEqual((result["state"], result["reason"], result["rows"], result["after"]), ("refused", reason, [], []), over)
            self.assertTrue(result["eligibility"], over)   # the chips stay

    def test_soft_conditions_keep_the_rows_and_warn(self):
        for over in (dict(start_iso="2026-11-20"), dict(start_iso=""), dict(clone=None), dict(match=None),
                     dict(clone={"state": "stale", "age_seconds": 30000, "reason": ""}),
                     dict(match={"status": "clone_unavailable", "candidates": []})):
            result = sheet(**over)
            self.assertEqual(result["state"], "ready", over)
            self.assertEqual(len(result["rows"]), len(LABELS), over)
            self.assertIn("check BackOffice yourself", result["warning"], over)
        self.assertEqual(sheet()["warning"], "")

    def test_snapshot_time_marks_the_salesforce_chips_and_stale_adds_the_warning(self):
        result = views.onboarding_sheet(co(), rules=RULES, today=TODAY, days_before=2, vm=True, clone=FRESH, match=CLEAR,
                                        as_of="2026-10-16 09:50 UTC", stale=True)
        chips = {item["label"]: item["detail"] for item in result["eligibility"]}
        for label in ("Salesforce approval", "Email domain", "Account UUID"):
            self.assertTrue(chips[label].endswith("(as of 2026-10-16 09:50 UTC)"), label)
        self.assertNotIn("as of", chips["Route"])
        self.assertNotIn("as of", chips["Timing window"])
        self.assertEqual(result["as_of"], "2026-10-16 09:50 UTC")
        self.assertEqual(result["stale_warning"], "Salesforce data may be out of date: re-check the CO in Salesforce before you start.")
        fresh = views.onboarding_sheet(co(), rules=RULES, today=TODAY, days_before=2, vm=True, clone=FRESH, match=CLEAR,
                                       as_of="2026-10-16 09:50 UTC", stale=False)
        self.assertEqual(fresh["stale_warning"], "")
        self.assertEqual(sheet()["as_of"], "")   # the desktop reads Salesforce live: no "as of"


class RulesPinnedToTheRunnerTests(unittest.TestCase):
    """The sheet follows the Dev CE-only mapping: pin every string against the runner."""

    def test_constants(self):
        self.assertEqual(RULES["first_name"], runner.CE_PRIMARY_USER_FIRST_NAME)
        self.assertEqual(RULES["last_name"], runner.CE_PRIMARY_USER_LAST_NAME)
        self.assertEqual(RULES["email_local"], runner.CE_PRIMARY_USER_EMAIL_LOCAL)
        self.assertEqual(RULES["email_domain"], runner.CE_USER_EMAIL_DOMAIN)
        self.assertEqual(RULES["quantities"], {"assets": "1", "domains": "1", "subdomains": "1"})

    def test_matches_the_dev_fill_plan(self):
        source = runner.CeFillSource("CO-0780", "rev", "001000000000000AAA", "State Land Service", "vzd.example", "Chile",
                                     date(2026, 6, 22), date(2029, 6, 21), "State Land Service - CE Only", "sls")
        plan = runner.build_ce_only_fill(source, TODAY)
        got = {item["label"]: item["value"] for item in sheet(comments="DealHub 2026-06-22 - 2029-06-21")["rows"]}
        self.assertEqual(got["Expiration date"], "2029-06-21")           # a multi-year deal: the subscription end (owner rule 2026-10-19)
        self.assertEqual(got["Expiration date"], plan["license_end"].isoformat())
        self.assertEqual(got["Company name"], plan["texts"]["Company name"])
        self.assertEqual(got["Company primary domain"], plan["texts"]["Company primary domain"])
        self.assertEqual(got["User email domains"], plan["texts"]["User email domains  (Comma Separated Values)"])
        self.assertEqual(got["Organization Email"], plan["texts"]["Organization Email"])
        self.assertEqual(got["Leaked Credentials scanned domains"],
                         plan["texts"]["Leaked Credentials scanned domains (Comma Separated Values)"])
        self.assertEqual(got["License Type"], plan["selects"]["Type"])
        self.assertEqual(got["Leaked Credentials scanning interval"], plan["selects"]["Leaked Credentials scanning interval"])
        self.assertEqual(got["Account Type"], plan["selects"]["Account Type"])
        self.assertEqual(got["Country"], plan["selects"]["Country"])
        self.assertEqual(got["Number of assets"], plan["texts"]["Number of assets"])
        self.assertEqual(got["Number of domains"], plan["texts"]["Number of domains"])
        self.assertEqual(got["Number of subdomains"], plan["texts"]["Number of subdomains"])
        self.assertEqual(plan["selects"]["Scanning interval"], "None")
        self.assertEqual(got["Scanning interval"], "None")


# --- the card on both CO routes ----------------------------------------------------------------------------------------

ROW = {"Name": "CO-0780", "Account_Name__c": "State Land Service", "Account_UUID__c": None,
       "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT, "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE,
       "Onboarding_Approval_Status__c": "Approved", "Email_Domains__c": "@vzd.example", "Onboarding_Comments__c": COMMENTS}


class PageBase(unittest.TestCase):
    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def review(self, row=ROW, vm=False, country="Chile", summary=None):
        with base.patched(self.env, unavailable=vm), patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            return dashboard.page_onboard_review(summary or base.summary_for(), base.EXTRAS, "", row=row, country=country)

    def prod(self, row=ROW, vm=False, country="Chile"):
        with base.patched(self.env, unavailable=vm), patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            return dashboard.page_detail_production("CO-0780", row, base.match(), country=country)

    def pages(self, **kwargs):
        # the production CO page (/co/CO-x?env=prod) uses the simplified guide of docs/50: see test_production_co_page.py
        return {"review": self.review(**kwargs)}

    @staticmethod
    def card(page):
        start = page.index("<section class='card sheet-card'")
        return page[start:page.index("</section>", start)]


class CardTests(PageBase):
    def test_present_once_after_the_readiness_card(self):
        for name, page in self.pages().items():
            self.assertEqual(page.count("sheet-card"), 1, name)
            self.assertIn("<h2 id='co-sheet-title' class='pill'>Add Account values</h2>", page, name)
            self.assertNotIn("Production onboarding sheet", page, name)
            self.assertLess(page.index("onboard-card"), page.index("sheet-card"), name)

    def test_only_the_values_no_repeated_checks_manual_steps_or_lock_line(self):
        # Owner feedback 2026-10-18: the checks are the readiness card; the runner enters these values itself.
        for name, page in self.pages().items():
            card = self.card(page)
            for text in ("Before you start", "After you click Confirm", "stays locked", "guidance only", "<ul class='checks'>"):
                self.assertNotIn(text, card, (name, text))
            self.assertIn("https://app.pentera.io", card)
            self.assertIn("<table class='dense'>", card)

    def test_one_table_row_per_field_with_exact_values(self):
        for name, page in self.pages().items():
            card = self.card(page)
            labels = [base.text(cell) for cell in re.findall(r"<tr><td>(.*?)</td><td><code>", card, flags=re.S)]
            self.assertEqual(labels, LABELS, name)
            for value in ("State Land Service - CE Only", "vzd.example", "milton.stevenson+sls@pentera.io", "Chile",
                          "Prepaid annual subscription", "2027-10-17", "2026-10-16"):
                self.assertIn("<code>" + value + "</code>", card, (name, value))

    def test_no_form_button_link_input_or_script(self):
        for name, page in self.pages().items():
            card = self.card(page)
            for tag in ("<form", "<button", "<input", "<a ", "<script", "<select", "<textarea", "onclick", "href="):
                self.assertNotIn(tag, card, (name, tag))
            self.assertNotIn("method='post'", card.lower())
            # the rest of the page keeps only its existing controls (the summary approval form)
            self.assertEqual(base.controls(page, allow=("/attended/review-approve", "Approve this summary")), [], name)

    def test_unlock_flag_cannot_add_a_control(self):
        with patch.object(dashboard.readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            for name, page in self.pages().items():
                for tag in ("<form", "<button", "<input", "<script"):
                    self.assertNotIn(tag, self.card(page), (name, tag))

    def test_supported_flags_untouched(self):
        from integration.onboarding import session_readiness
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, False)
        self.assertIs(dashboard.readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    def test_escaping(self):
        evil = {**ROW, "Account_Name__c": "<script>alert(1)</script> & Co", "Email_Domains__c": "x\"><img src=x onerror=1>.example",
                "Onboarding_Comments__c": "<b>2026-10-18 - 2027-10-17</b>"}
        for name, page in self.pages(row=evil, country="<i>Chile</i>").items():
            self.assertNotIn("<script>alert", page, name)
            self.assertNotIn("<img", page, name)
            self.assertNotIn("<i>Chile", page, name)
        evil_name = {**ROW, "Account_Name__c": "<b>Evil</b> Co"}
        for name, page in self.pages(row=evil_name, country="<i>Chile</i>").items():
            card = self.card(page)
            self.assertIn("&lt;b&gt;Evil&lt;/b&gt; Co - CE Only", card, name)
            self.assertIn("&lt;i&gt;Chile&lt;/i&gt;", card, name)
            self.assertNotIn("<b>Evil", card, name)

    def test_no_country_means_that_row_only_is_unavailable(self):
        for name, page in self.pages(country=None).items():
            card = self.card(page)
            self.assertIn("<code>" + UNAVAILABLE + "</code>", card, name)
            self.assertEqual(card.count("<code>" + UNAVAILABLE + "</code>"), 1, name)
            self.assertIn("chip-warn'>Unavailable</span>", card, name)

    def test_two_domains_refuses_with_the_reason_and_no_table(self):
        row = {**ROW, "Email_Domains__c": "@a.example, @b.example"}
        for name, page in self.pages(row=row).items():
            card = self.card(page)
            self.assertIn("Do not onboard this CO:", card, name)
            self.assertIn(views.SHEET_REFUSALS["one_email_domain_required"], card, name)
            self.assertNotIn("<table", card, name)
            self.assertNotIn("stays locked", card, name)

    def test_uuid_present_refuses(self):
        row = {**ROW, "Account_UUID__c": "a" * 32}
        for name, page in self.pages(row=row).items():
            card = self.card(page)
            self.assertIn(views.SHEET_REFUSALS["account_uuid_set"], card, name)
            self.assertNotIn("<table", card, name)
            self.assertNotIn("a" * 32, card, name)

    def test_unreadable_row_refuses_on_the_review_page(self):
        card = self.card(self.review(row=None))
        self.assertIn(views.SHEET_REFUSALS["account_uuid_unread"], card)
        self.assertNotIn("<table", card)

    def test_not_ce_only_says_not_applicable(self):
        surface = base.summary_for(route=dashboard.SURFACE_ENGINE)
        card = self.card(self.review(summary=surface))
        self.assertIn("Not applicable: this CO is not a CE-only New Product Onboarding.", card)
        self.assertNotIn("<table", card)

    def test_sheet_failure_fails_closed(self):
        with patch.object(views, "onboarding_sheet", side_effect=RuntimeError("boom")):
            for name, page in self.pages().items():
                card = self.card(page)
                self.assertIn("The values are " + UNAVAILABLE + ".", card, name)
                self.assertNotIn("<table", card, name)


class VmModeTests(PageBase):
    """VM mode: only the published snapshot fields (exactly DETAIL_FIELDS); the country is not published."""

    VM_ROW = {field: None for field in dashboard.DETAIL_FIELDS}
    VM_ROW.update(ROW)

    def test_rows_from_the_snapshot_row_with_the_country_unavailable(self):
        for name, page in self.pages(row=self.VM_ROW, vm=True, country=None).items():
            card = self.card(page)
            labels = [base.text(cell) for cell in re.findall(r"<tr><td>(.*?)</td><td><code>", card, flags=re.S)]
            self.assertEqual(labels, LABELS, name)
            self.assertEqual(card.count("<code>" + UNAVAILABLE + "</code>"), 1, name)
            self.assertIn("<code>State Land Service - CE Only</code>", card, name)
            self.assertIn("<code>2027-10-17</code>", card, name)
            self.assertIn("The published snapshot does not carry the country.", card, name)
            for tag in ("<form", "<button", "<input", "<a ", "<script"):
                self.assertNotIn(tag, card, (name, tag))

    def test_clone_check_is_unavailable_when_nothing_is_published(self):
        with base.patched(self.env, unavailable=True), patch.object(dashboard, "vm_mode", return_value=True), \
                patch.object(dashboard, "inventory_root", return_value=self.env.dir / "missing"), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            page = dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=self.VM_ROW)
        start = page.index("<section class='card onboard-card'")   # the checks live in the readiness card only
        ready = page[start:page.index("</section>", start)]
        self.assertRegex(ready, r"Clone fresh under 6 h.*?chip-neutral'>Unavailable</span>")

    def test_the_card_shows_the_snapshot_time_and_the_stale_warning(self):
        for stale in (False, True):
            with patch.object(dashboard, "_snapshot_as_of", return_value=("2026-10-16 09:50 UTC", stale)):
                for name, page in self.pages(row=self.VM_ROW, vm=True, country=None).items():
                    card = self.card(page)
                    self.assertIn("Salesforce data (approval, UUID, domains) was published at 2026-10-16 09:50 UTC; the tenant clone is separate and", card, name)
                    self.assertEqual(card.count("(as of 2026-10-16 09:50 UTC)"), 0, name)   # the checks are not repeated
                    self.assertEqual("Salesforce data may be out of date: re-check the CO in Salesforce before you start." in card,
                                     stale, name)

    def test_no_snapshot_time_on_the_desktop(self):
        for name, page in self.pages().items():
            self.assertNotIn("as of", self.card(page), name)

    def test_unreadable_snapshot_time_is_unknown_and_stale(self):
        with patch.object(dashboard, "vm_mode", return_value=True), patch.object(dashboard, "snapshot_root", return_value=None):
            self.assertEqual(dashboard._snapshot_as_of(), ("unknown", True))

    def test_the_country_read_never_runs_on_the_vm(self):
        calls = []
        dashboard.clear_display_cache()
        with patch.object(dashboard, "vm_mode", return_value=True), \
                patch.object(dashboard, "sf_json", side_effect=lambda *a, **k: calls.append(a) or {"status": 0, "result": {"records": [
                    {"Account_Country__c": "Chile"}]}}):
            self.assertIsNone(dashboard._read_sheet_country("CO-0780"))   # no snapshot: unavailable, not the live value
        self.assertEqual(calls, [])


class CountryReadTests(unittest.TestCase):
    def read(self, response=None, error=None, reference="CO-0780"):
        stub = patch.object(dashboard, "sf_json", side_effect=error) if error else patch.object(dashboard, "sf_json", return_value=response)
        with stub as call, patch.object(dashboard, "vm_mode", return_value=False):
            dashboard.clear_display_cache()
            return dashboard._read_sheet_country(reference), call

    def test_reads_one_fixed_field_of_one_co(self):
        got, call = self.read({"status": 0, "result": {"records": [{"Account_Country__c": " Chile "}]}})
        self.assertEqual(got, "Chile")
        query = call.call_args.args[0]
        self.assertEqual(query[:3], ["data", "query", "--query"])
        self.assertIn("SELECT Account_Country__c FROM Customer_Onboarding__c WHERE Name = 'CO-0780' LIMIT 2", query[3])
        self.assertTrue(query[3].startswith("SELECT"))

    def test_any_problem_is_none(self):
        for response in ({"status": 1, "result": {"records": []}}, {"status": 0, "result": {"records": []}},
                         {"status": 0, "result": {"records": [{}, {}]}}, {"status": 0, "result": {"records": [{"Account_Country__c": 5}]}},
                         {"status": 0}, "garbage"):
            self.assertIsNone(self.read(response)[0], response)
        self.assertIsNone(self.read(error=dashboard.ReadUnavailable())[0])

    def test_bad_reference_makes_no_read(self):
        got, call = self.read({"status": 0, "result": {"records": []}}, reference="CO-1'; DROP")
        self.assertIsNone(got)
        call.assert_not_called()


class NoWriteRouteTests(unittest.TestCase):
    def test_license_type_constant_is_the_runners(self):
        source = runner.CeFillSource("CO-0780", "rev", "001000000000000AAA", "A", "a.example", "Chile", date(2026, 10, 18),
                                     date(2027, 10, 17), "A - CE Only", "a")
        self.assertEqual(dashboard.SHEET_LICENSE_TYPE, runner.build_ce_only_fill(source, TODAY)["selects"]["Type"])

    def test_a_post_to_every_co_route_is_refused_and_renders_no_sheet(self):
        from integration.tests import test_dev_prod_areas as areas_module

        self.assertFalse(any("sheet" in route or route.startswith(("/onboard", "/co/")) for route in dashboard.POST_ROUTES))
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())
        for path in ("/onboard/CO-0780", "/co/CO-0780", "/co/CO-0780?env=prod", "/onboard/CO-0780/sheet", "/sheet",
                     "/co/CO-0780/onboarding-sheet", "/prod/sheet"):
            request = areas_module.FakeRequest(path)
            with patch.object(dashboard, "login_required", return_value=False), \
                    patch.object(dashboard, "request_origin_problem", return_value=None), \
                    patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce")):
                dashboard.Handler.do_POST(request)
            self.assertEqual([status for status, _ in request.pages], [404], path)
            self.assertFalse(any("sheet-card" in page or "Production onboarding sheet" in page for _, page in request.pages), path)
            self.assertEqual(request.redirects, [], path)

    def test_views_module_stays_pure(self):
        source = Path(views.__file__).read_text(encoding="utf-8")
        start = source.index("# --- Production onboarding sheet")
        for word in ("subprocess", "urllib", "requests", "socket", "open(", "write_text", "datetime.now", "date.today", "os.environ"):
            self.assertNotIn(word, source[start:], word)


if __name__ == "__main__":
    unittest.main()


class OnboardedAndUnverifiedMatchTests(unittest.TestCase):
    """Only exactly 'clear' may lead to a creatable sheet; every other production status refuses or blocks (review 2026-10-17)."""

    def refused(self, match, **over):
        result = sheet(match=match, **over)
        self.assertEqual(result["state"], "refused", match)
        return result

    def test_onboarded_always_blocks_with_the_clear_reason_even_with_an_empty_uuid_read(self):
        for warnings in ([], ["spycloud_off"], ["tenant_deleted"]):
            match = {"status": "onboarded", "reason": "x", "candidates": [], "review": {"warnings": warnings}}
            result = self.refused(match)
            self.assertEqual(result["reason"], "already_onboarded")
            self.assertEqual(result["rows"], [])
            self.assertEqual([i["status"] for i in result["eligibility"] if i["label"] == "Clone and production match"], ["blocked"])
            self.assertIn("Already onboarded: tenant found in production", views.SHEET_REFUSALS["already_onboarded"])

    def test_uuid_not_in_clone_and_not_checked_refuse(self):
        self.assertEqual(self.refused({"status": "uuid_not_in_clone", "reason": "", "candidates": []})["reason"], "uuid_not_in_clone")
        self.assertEqual(self.refused({"status": "not_checked", "reason": "production_inputs_not_prepared", "candidates": []})["reason"],
                         "match_not_checked")

    def test_snapshot_uuid_set_while_the_read_shows_empty_is_a_disagreement(self):
        match = {"status": "not_in_production", "reason": "", "candidates": [], "tenants_checked": 5, "co_uuid_set": True}
        result = self.refused(match, uuid_state="empty")
        self.assertEqual(result["reason"], "uuid_sources_disagree")
        self.assertEqual([i["status"] for i in result["eligibility"] if i["label"] == "Account UUID"], ["blocked"])
        self.assertNotEqual(sheet(match={**match, "co_uuid_set": False}, uuid_state="empty")["state"], "refused")

    def test_the_checklist_blocks_onboarded_and_disagreement(self):
        base = dict(vm=True, locked=True, lock_reason=None, clone={"state": "fresh", "age_seconds": 60},
                    domain_count=1, uuid_state="empty", ce_route=True, start_iso="2026-10-18", today=date(2026, 10, 17),
                    days_before=2, approval=None, create=None)
        items = {i["key"]: i for i in views.onboard_checklist(match={"status": "onboarded", "reason": "", "candidates": [],
                                                                      "review": {"warnings": []}}, **base)}
        self.assertEqual(items["duplicate"]["status"], "blocked")
        items = {i["key"]: i for i in views.onboard_checklist(match={"status": "not_in_production", "candidates": [],
                                                                      "co_uuid_set": True}, **base)}
        self.assertEqual(items["data"]["status"], "blocked")


class OnboardedGuideTests(unittest.TestCase):
    ONBOARDED = {"status": "onboarded", "reason": "x", "candidates": [], "tenants_checked": 5,
                 "review": {"line": "Onboarded - tenant enabled, license active until 2027-10-18 (matches DealHub end rule: expected 2027-10-18).",
                            "warnings": []}}

    def guide(self, match, **over):
        return views.production_co_guide(sheet(match=match, uuid_state="set", **over), clone={"state": "fresh", "age_seconds": 600})

    def test_onboarded_is_a_neutral_headline_with_nothing_to_enter_and_the_create_still_blocked(self):
        result = self.guide(self.ONBOARDED)
        self.assertEqual(result["verdict"], "onboarded")
        self.assertTrue(result["headline"].startswith("Onboarded: tenant found in production, enabled, license active until 2027-10-18"))
        self.assertNotIn("Blocked", result["headline"])
        self.assertEqual((result["rows"], result["after"]), ([], []))
        uuid_check = next(item for item in result["checks"] if item["detail"] == "Set (matches tenant)")
        self.assertEqual((uuid_check["status"], uuid_check["label"]), ("ok", "Account UUID"))

    def test_a_co_this_dashboard_onboarded_has_no_blocked_uuid_row(self):
        """The nightly clone may not hold today's create, so the sheet says UUID "Already set": the recorded evidence wins."""
        result = self.guide(match={"status": "uuid_not_in_clone", "reason": "x", "candidates": []})
        uuid_check = next(item for item in result["checks"] if item["label"] in ("Account UUID", "Account UUID empty"))
        self.assertEqual(uuid_check["status"], "blocked")
        views.apply_own_onboarding(result, "Onboarded in production by this dashboard: x; stage Account Scanning")
        self.assertEqual((result["verdict"], result["rows"], result["after"], result["leave"]), ("onboarded", [], [], ""))
        self.assertTrue(result["headline"].endswith("stage Account Scanning"))
        uuid_check = next(item for item in result["checks"] if item["label"] == "Account UUID")
        self.assertEqual(uuid_check["status"], "ok")
        self.assertIn("this dashboard", uuid_check["detail"])
        self.assertNotIn("blocked", [item["status"] for item in result["checks"] if item["label"] == "Account UUID"])

    def test_onboarded_with_warnings_is_amber_review(self):
        match = {**self.ONBOARDED, "review": {"line": "Onboarded - check: SpyCloud off or unknown.", "warnings": ["spycloud_off"]}}
        result = self.guide(match)
        self.assertEqual(result["verdict"], "onboarded_review")
        self.assertTrue(result["headline"].startswith("Onboarded: review"))
        self.assertEqual(sheet(match=match, uuid_state="set")["rows"], [])

    def test_a_uuid_with_no_or_a_different_tenant_stays_red_blocked(self):
        for status in ("uuid_not_in_clone", "exists_matches", "not_in_production"):
            result = self.guide({"status": status, "reason": "", "candidates": [], "tenants_checked": 5})
            self.assertEqual(result["verdict"], "blocked", status)
            self.assertTrue(result["headline"].startswith("Blocked"), status)
            self.assertEqual([i["status"] for i in result["checks"] if i["label"].startswith("Account UUID")], ["blocked"], status)
