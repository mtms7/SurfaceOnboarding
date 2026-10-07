"""Sidebar search (owner request 2026-10-07): GET /search, read-only, no script, no new Salesforce query.

Every reader is stubbed; the queue read is the existing cached queue_source_rows.
"""
from __future__ import annotations

import contextlib
import re
import unittest
from unittest.mock import patch

from integration.tests import test_co_page_layout as layout
from integration.tests.test_dev_prod_areas import FakeRequest, areas, forms, get
import tools.serve_attended_open_onboardings_dashboard as dashboard

ROWS = [
    {"Name": "CO-0767", "Account__r.Name": "Zeta Holdings", "Main_Domain__c": "zeta-corp.example",
     "Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
     "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New"},
    {"Name": "CO-0801", "Account__r.Name": "Acme <b>Bold</b> Ltd", "Onboarding_Product__c": "Surface",
     "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New"},
]


def search_page(query, **kwargs):
    with areas(**kwargs):
        target, page = dashboard.search_response(query)
    return target, page


class FormOnEveryPageTests(unittest.TestCase):
    def setUp(self):
        for name in ("closed_history", "queue_start_dates"):
            patcher = patch.object(dashboard, name, side_effect=dashboard.ReadUnavailable())
            patcher.start()
            self.addCleanup(patcher.stop)

    FORM = "<form class='sidesearch' method='get' action='/search' role='search'>"

    def check(self, page):
        self.assertIn(self.FORM, page)
        form = page[page.index(self.FORM):page.index("</form>", page.index(self.FORM))]
        self.assertIn("name='q'", form)
        self.assertIn("maxlength='80'", form)
        self.assertIn("placeholder='Search CO or account'", form)
        self.assertIn(">Search</button>", form)
        self.assertLess(page.index(self.FORM), page.index("navgroup dev"))  # above the groups, inside the sidebar
        self.assertGreater(page.index(self.FORM), page.index("<aside"))
        self.assertNotIn("<script", page.lower())

    def test_form_is_in_the_shell_for_every_page_family(self):
        with areas():
            for path in ("/", "/history", "/prod", "/dev/onboarded", "/connection"):
                request = get(path)
                if request.pages:
                    self.check(request.pages[-1][1])
        self.check(dashboard._app_shell("t", "", active="onboardings"))

    def test_form_is_on_the_queue_the_history_and_the_prod_pages(self):
        with areas():
            for path in ("/", "/history", "/prod"):
                request = get(path)
                self.assertTrue(request.pages, path)
                self.check(request.pages[-1][1])

    def test_form_is_on_the_co_page(self):
        row = layout.row_for({})
        self.check(layout.render(row))

    def test_mobile_css_keeps_the_form_visible(self):
        css = dashboard.PENTERA_CSS
        self.assertIn(".sidesearch{", css)
        mobile = css[css.index("@media(max-width:760px){.sidesearch"):]
        self.assertNotIn("display:none", mobile[:mobile.index("}}") + 2])


class RedirectTests(unittest.TestCase):
    def test_co_variants_redirect_to_the_canonical_co_page(self):
        for text in ("CO-0767", "co-0767", "767", "co 767", "  CO   0767 ", "0767", "CO0767", "Co-767"):
            with self.subTest(text=text):
                self.assertEqual(dashboard.search_response(text), ("/co/CO-0767", ""))

    def test_redirect_goes_through_the_get_route_and_needs_no_known_source(self):
        with areas(rows=[]):
            request = get("/search?q=co+9999")
        self.assertEqual(request.redirects, ["/co/CO-9999"])
        self.assertEqual(request.pages, [])

    def test_text_that_is_not_a_reference_goes_to_the_results_page(self):
        for text in ("CO-", "CO-12345678901", "12345678901", "CO-07x7", "Zeta", "co 7 67", "CO-0767 extra"):
            with self.subTest(text=text):
                target, page = search_page(text)
                self.assertIsNone(target)
                self.assertIn("Search: " + text, page)


class ResultsTests(unittest.TestCase):
    def test_matches_by_account_name_domain_and_co_fragment_case_insensitively(self):
        for query, ref in (("zeta", "CO-0767"), ("ZETA-CORP.EXAMPLE", "CO-0767"), ("acme", "CO-0801"), ("0801-", None)):
            with self.subTest(query=query):
                target, page = search_page(query, rows=ROWS)
                self.assertIsNone(target)
                if ref:
                    self.assertIn("href='/co/" + ref + "'", page)
        _target, page = search_page("zeta", rows=ROWS)
        self.assertNotIn("CO-0801", page)
        for header in ("Account", "Route / Case", "Where", "Approval / Stage"):
            self.assertIn(">" + header + "</th>", page)
        self.assertIn("Open queue", page)
        self.assertIn("Approved · New", page)
        self.assertIn("zeta-corp.example", page)

    def test_local_evidence_matches_without_a_queue_row_and_shows_where(self):
        _target, page = search_page("Beta", rows=[])
        self.assertIn("No CO matches", page)
        # A CO known only from local records matches on the tenant name or domain of the local Dev snapshot.
        with patch.object(dashboard, "_search_inventory_names", return_value={
                "CO-0701": ("Quartz Tenant", "quartz.example"), "CO-0706": ("Quartz Two", "q2.example")}):
            _target, page = search_page("QUARTZ.example", rows=[])
            self.assertIn("href='/co/CO-0701'", page)
            self.assertNotIn("CO-0706", page)
            self.assertIn("Onboarded on Dev", page)
            self.assertIn("Quartz Tenant", page)
            _target, page = search_page("quartz two", rows=[])
        self.assertIn("href='/co/CO-0706'", page)
        self.assertIn("Local run records", page)

    def test_open_row_and_dev_evidence_are_both_reported(self):
        _target, page = search_page("Acme CE")
        self.assertIn("href='/co/CO-0701'", page)
        self.assertIn("Open queue, Onboarded on Dev", page)

    def test_no_matches_message(self):
        _target, page = search_page("nothing-like-this", rows=ROWS)
        self.assertIn("No CO matches", page)
        self.assertNotIn("<table", page)

    def test_results_are_capped_at_50_rows(self):
        many = [{**ROWS[1], "Name": "CO-%04d" % number, "Account__r.Name": "Bulk Account %d" % number} for number in range(1000, 1075)]
        _target, page = search_page("bulk account", rows=many)
        self.assertEqual(page.count("<tr><td class='stick'>"), 50)
        self.assertIn("first 50 of 75 matches", page)

    def test_page_uses_the_neutral_tools_pill_and_has_only_the_get_search_form(self):
        _target, page = search_page("zeta", rows=ROWS)
        self.assertIn("envpill tools'>TOOLS<", page)
        self.assertNotIn("post", [m.lower() for m in re.findall(r"<form[^>]*method='([^']*)'", page)])
        self.assertEqual(len(re.findall(r"<form[^>]*>", page)), 1)
        self.assertEqual(forms(page), [])
        self.assertNotIn("<script", page.lower())

    def test_through_the_get_route_the_results_page_is_a_200(self):
        with areas(rows=ROWS):
            request = get("/search?q=zeta")
        self.assertEqual(request.redirects, [])
        self.assertEqual(request.pages[0][0], 200)
        self.assertIn("Search: zeta", request.pages[0][1])

    def test_queue_read_failure_shows_local_matches_with_a_note(self):
        with areas(), patch.object(dashboard, "queue_source_rows", side_effect=dashboard.ReadUnavailable()), \
                patch.object(dashboard, "_search_inventory_names", return_value={"CO-0701": ("Quartz Tenant", "quartz.example")}):
            request = get("/search?q=quartz")
        status, page = request.pages[0]
        self.assertEqual(status, 200)
        self.assertIn("showing local matches only", page)
        self.assertIn("href='/co/CO-0701'", page)
        self.assertNotIn("Open queue", page)

    def test_search_never_calls_salesforce_directly(self):
        with areas(rows=ROWS), patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce")):
            self.assertIsNone(dashboard.search_response("zeta")[0])


class SafetyTests(unittest.TestCase):
    def test_query_is_escaped_in_the_title_the_heading_and_the_page(self):
        payload = "<script>alert(1)</script>"
        _target, page = search_page(payload)
        self.assertNotIn(payload, page)
        self.assertNotIn("<script", page.lower())
        self.assertIn("Search: &lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_values_from_rows_are_escaped(self):
        _target, page = search_page("bold", rows=ROWS)
        self.assertIn("Acme &lt;b&gt;Bold&lt;/b&gt; Ltd", page)
        self.assertNotIn("<b>Bold</b>", page)

    def test_too_long_or_control_character_text_is_rejected(self):
        for text in ("a" * 81, "ab\x00cd", "ab\ncd", "tab\there", "x\x7f"):
            with self.subTest(text=repr(text)):
                target, page = search_page(text)
                self.assertIsNone(target)
                self.assertIn("Search text too long or invalid", page)
        # Exactly 80 characters is accepted.
        _target, page = search_page("a" * 80)
        self.assertNotIn("too long or invalid", page)
        # A reference-shaped but over-long string is invalid, not redirected.
        target, page = search_page("CO-" + "0" * 80)
        self.assertIsNone(target)
        self.assertIn("too long or invalid", page)

    def test_no_post_route_or_form_was_added(self):
        self.assertNotIn("/search", dashboard.POST_ROUTES)
        self.assertEqual(re.findall(r"<form[^>]*>", dashboard.SEARCH_FORM), [
            "<form class='sidesearch' method='get' action='/search' role='search'>"])

    def test_unauthenticated_search_redirects_to_login_like_other_pages(self):
        for path in ("/search?q=zeta", "/search?q=767", "/"):
            request = FakeRequest(path)
            with patch.object(dashboard, "login_required", return_value=True), \
                    patch.object(dashboard, "dashboard_operator", return_value=None), \
                    patch.object(dashboard, "queue_source_rows", side_effect=AssertionError("no read before login")):
                dashboard.Handler.do_GET(request)
            self.assertEqual(request.redirects, ["/login"], path)
            self.assertEqual(request.pages, [])

    def test_signed_in_search_is_served(self):
        request = FakeRequest("/search?q=767")
        previous = dashboard._current_operator.get()
        self.addCleanup(dashboard._current_operator.set, previous)  # do_GET sets it in the calling context
        with areas(), patch.object(dashboard, "login_required", return_value=True), \
                patch.object(dashboard, "dashboard_operator", return_value="Op"):
            dashboard.Handler.do_GET(request)
        self.assertEqual(request.redirects, ["/co/CO-0767"])


if __name__ == "__main__":
    unittest.main()
