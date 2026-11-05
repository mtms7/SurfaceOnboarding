"""Owner request 2026-11-05: on every tab of the production queue (All / Ready / Blocked / Approved / Created) each column sorts
with a small arrow, finished rows have a date (the new "Done" column: the verified create time, else Salesforce's last update),
each tab has its own default sort, and a small filter bar (product, stage, check, search) plus header menus filter the view.
The long check reason folds under its chip; a CO link keeps the view so its page's "Onboarding" crumb returns to it.

Server-side only (the page CSP allows no script). Hermetic: the same fake reads as the landing tests.
"""

from __future__ import annotations

import re
import unittest
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.tests import test_production_first_dashboard as landing

REF = re.compile(r"<a class='co' href='/onboard/(CO-\d+)(?:\?back=[^']*)?'>")


class QueueSortFilterTests(landing.LandingTests):
    def refs(self, path):
        return REF.findall(self.page(path))

    def head(self, page):
        return page[page.index("<thead>"):page.index("</thead>")]

    def test_every_column_but_approval_and_create_sorts(self):
        head = self.head(self.page())
        keys = re.findall(r"href='/\?[^']*sort=([a-z]+)", head)
        for key in ("days", "co", "account", "product", "stage", "check", "done", "age"):
            self.assertIn(key, keys)
        self.assertIn("aria-label='Sort by Done, descending'", head)  # a first click on Done sorts newest first
        self.assertIn("<th scope='col' aria-sort='ascending'><a class='sort' href=", head)  # Start is the All default

    def test_tab_defaults(self):
        created = self.head(self.page("/?filter=created"))
        self.assertRegex(created, r"aria-sort='descending'><a class='sort'[^>]*>Done<")
        blocked = self.head(self.page("/?filter=blocked"))
        self.assertRegex(blocked, r"aria-sort='descending'><a class='sort'[^>]*>Age<")

    def test_sort_by_account_both_ways(self):
        names = {"CO-0780": "State Land Service", "CO-0781": "Demo Logistics", "CO-0782": "Two Live Paid",
                 "CO-0784": "Stale Clone Co", "CO-0785": "Already Created"}
        ascending = self.refs("/?sort=account&dir=asc")
        self.assertEqual(ascending, sorted(names, key=lambda ref: names[ref].casefold()))
        self.assertEqual(self.refs("/?sort=account&dir=desc"), list(reversed(ascending)))

    def test_bad_sort_or_direction_falls_back_to_the_tab_default(self):
        self.assertEqual(self.refs("/?sort=evil&dir=sideways"), self.refs("/"))

    def test_done_rows_have_a_date(self):
        with patch.dict(dashboard.QUEUE_LAST_MODIFIED, {"CO-0785": "2026-10-15T09:00:00.000+0000"}, clear=True):
            page = self.page("/?filter=created")
        row = self.row_of(page, "CO-0785")
        self.assertIn("<td class='c-done'><b>Oct 16</b>", row)  # created by this dashboard: the verified create time wins
        self.assertIn("<b>Oct 17</b><span class='muted'>done</span>", row)  # the start date stays visible on a done row

    def test_done_date_falls_back_to_salesforce_last_update(self):
        record = {"recorded_at": "2026-11-05T20:39:06Z"}
        with patch.dict(dashboard.QUEUE_LAST_MODIFIED, {"CO-0901": "2026-11-04T10:00:00.000+0000"}, clear=True),                 patch.object(dashboard, "vm_mode", return_value=False):
            self.assertEqual(dashboard._queue_done_at("CO-0901", record), "2026-11-05T20:39:06Z")
            self.assertEqual(dashboard._queue_done_at("CO-0901", None), "2026-11-04T10:00:00.000+0000")
            self.assertEqual(dashboard._queue_done_at("CO-0902", None), "")
        with patch.dict(dashboard.QUEUE_LAST_MODIFIED, {}, clear=True), patch.object(dashboard, "vm_mode", return_value=True),                 patch.object(dashboard, "snapshot_detail_row", return_value={"LastModifiedDate": "2026-11-03T08:00:00.000+0000"}):
            self.assertEqual(dashboard._queue_done_at("CO-0903", None), "2026-11-03T08:00:00.000+0000")

    def test_filters_combine_with_tabs_and_counts(self):
        page = self.page("/?q=demo")
        self.assertEqual(REF.findall(page), ["CO-0781"])
        counts = dict(re.findall(r"class='fchip(?: on)?' href='[^']+'>([A-Za-z ]+) <b>(\d+)</b>", page))
        self.assertEqual(counts["All"], "1")
        self.assertEqual(counts["Blocked"], "1")
        self.assertIn("Showing 1 of 6 open", page)
        self.assertEqual(self.refs("/?filter=blocked&check=blocked"), sorted(self.refs("/?filter=blocked&check=blocked"),
                                                                              key=lambda ref: self.refs("/?filter=blocked").index(ref)))
        self.assertEqual(self.refs("/?check=clear"), ["CO-0780"])
        self.assertEqual(self.refs("/?stage=New"), [])

    def test_bad_filter_values_are_dropped_and_escaped(self):
        page = self.page("/?product=Evil&stage=Nope&check=x&q=%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertEqual(REF.findall(page), self.refs("/"))
        self.assertNotIn("<script", page)
        self.assertNotIn("alert(1)", page)

    def test_clear_filters_keeps_the_tab_and_no_match_says_so(self):
        page = self.page("/?filter=blocked&q=nothing-matches")
        self.assertIn("No COs match these filters.", page)
        self.assertIn("href='/?filter=blocked'>Clear filters</a>", page)

    def test_the_filter_bar_is_a_get_form_with_no_script(self):
        page = self.page("/?filter=ready")
        form = re.search(r"<form class='fform' method='get' action='/'>.*?</form>", page, flags=re.S).group(0)
        self.assertIn("<input type='hidden' name='filter' value='ready'>", form)
        for name in ("product", "stage", "check"):
            self.assertIn("<select name='" + name + "'>", form)
        self.assertNotIn("<script", page)

    def test_header_menus_set_one_filter(self):
        head = self.head(self.page("/?filter=ready"))
        self.assertIn("<details class='hmenu'>", head)
        self.assertIn("href='/?filter=ready&amp;check=blocked'", head)

    def test_long_check_text_folds_under_the_chip(self):
        row = self.row_of(self.page(), "CO-0785")
        self.assertIn("<details class='why'><summary><span class='chip", row)
        self.assertIn("</summary><span class='sub'>", row)

    def test_co_links_keep_the_view_and_back_is_safe(self):
        page = self.page("/?filter=blocked&sort=co")
        self.assertIn("/onboard/CO-0781?back=%2F%3Ffilter%3Dblocked%26sort%3Dco%26dir%3Dasc", page)
        self.assertNotIn("?back=", self.page("/"))  # the plain landing needs no back link
        self.assertEqual(dashboard.safe_back("/?filter=blocked&sort=co"), "/?filter=blocked&sort=co")
        for bad in ("https://evil.example/", "//evil.example", "/onboard/CO-0781", "/?x=<script>", "javascript:alert(1)", None):
            self.assertEqual(dashboard.safe_back(bad), "/", bad)

    def test_queue_filters_allow_lists(self):
        picked = dashboard.queue_filters({"product": ["Surface"], "stage": ["User Created"], "check": ["onboarded"],
                                          "q": ["  Ion   Bank  "]})
        self.assertEqual(picked, {"product": "Surface", "stage": "User Created", "check": "onboarded", "q": "Ion Bank"})
        self.assertEqual(dashboard.queue_filters({"q": ["x" * 200]})["q"], "x" * dashboard.PROD_SEARCH_MAX)
        self.assertEqual(dashboard.queue_filters(None), {"product": "", "stage": "", "check": "", "q": ""})


# Only this class's own tests (the inherited landing tests already run in their own module).
for _name in [name for name in vars(landing.LandingTests) if name.startswith("test_")]:
    if _name not in vars(QueueSortFilterTests):
        setattr(QueueSortFilterTests, _name, None)


class CrumbTests(unittest.TestCase):
    def test_the_crumb_returns_to_the_remembered_view(self):
        token = dashboard._QUEUE_BACK.set("/?filter=created")
        try:
            env = landing.Env()
            self.addCleanup(env.tmp.cleanup)
            with landing.patched(env):
                html = dashboard.page_onboard_review(landing.summary_for(), landing.EXTRAS, "", row=landing.ROW)
        finally:
            dashboard._QUEUE_BACK.reset(token)
        self.assertIn("<a class='crumb' href='/?filter=created'>&larr; Onboarding</a>", html)


if __name__ == "__main__":
    unittest.main()
