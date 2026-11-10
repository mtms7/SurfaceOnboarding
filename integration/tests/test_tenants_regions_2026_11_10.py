"""Owner request 2026-11-10: Production > Tenants (/prod/tenants) shows the EU and the US Redash clone.

* ``?region=`` all (default) / eu / us, carried with the search, filter and sort parameters.
* "All" is one table (each row names its region; sortable by region); rows are never merged across regions (a tenant is
  region + account UUID).
* One header line per region (the rest in a native <details>); a missing, stale, wrong-region or unconfigured region
  shows "<region> clone unavailable: <reason>" and never counts as "no tenants".
* Minimal layout (owner request 2026-11-10, later): one filter row, a 6-column table, per-row native <details>, no script.

Hermetic: fake rows, temporary folders, patched clock; no network, Redash, Salesforce or BackOffice.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import leonardo_inventory as inventory
from integration.onboarding import redash_inventory as redash
from integration.tests import test_redash_v2 as v2
from integration.tests.test_us_clone_2026_11_09 import unpinned

NOW = v2.NOW
FRESH_MODIFIED = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000000")


def us_row(index: int, **overrides):
    values = {"_id": f"75a0{index:020x}", "accountUuid": f"{0xA000 + index:032x}", "accountName": f"US Tenant {index}",
              "accountDomain": f"us{index}.example.com", "_modified": FRESH_MODIFIED}
    values.update(overrides)
    return v2.row253(index, **values)


def eu_row(index: int, **overrides):
    return v2.row253(index, **{"_modified": FRESH_MODIFIED, **overrides})


def write_clone(root: Path, env: str, query_id: int, rows: list[dict], age=timedelta(minutes=30)) -> dict:
    """A collector-shaped v2 clone snapshot (latest.json + snapshot file) of ``env`` from ``query_id``."""
    normalized = redash.normalize(v2.reply(rows, (NOW - age).strftime("%Y-%m-%dT%H:%M:%S")), query_id)
    payload = inventory.snapshot_payload(
        inventory.require_environment(env), inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
        acquisition=inventory.REDASH_ACQUISITION, source=redash.source_meta(normalized, NOW, refreshed=False),
        expected_paths=redash.expected_paths(query_id))
    payload = redash.attach_v2(payload, normalized)
    inventory.write_snapshot(payload, root, keep=4)
    return payload


def body_rows(page: str) -> list[str]:
    match = re.search(r"<tbody>(.*?)</tbody>", page, re.S)
    return re.findall(r"<tr>.*?</tr>", match.group(1), re.S) if match else []


def row_names(page: str) -> list[str]:
    return [re.search(r"<span class='tm-name'>([^<]*)</span>", row).group(1) for row in body_rows(page)]


def row_regions(page: str) -> list[str]:
    return [re.search(r"<span class='tm-meta'>(EU|US) · ", row).group(1) for row in body_rows(page)]


def cells(row: str) -> list[str]:
    return re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)


class RegionsCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        for target in (patch.object(dashboard, "clone_root", return_value=self.root),
                       patch.object(dashboard, "production_now", return_value=NOW),
                       patch.object(dashboard, "_audit_live_by_uuid", return_value={}),
                       patch.object(dashboard, "vm_mode", return_value=False),
                       patch.object(dashboard, "attended_leonardo_readbacks",
                                    side_effect=AssertionError("Dev readbacks never apply to production"))):
            target.start()
            self.addCleanup(target.stop)

    def eu(self, count=3, **kwargs):
        return write_clone(self.root, "prod-clone", 253, [eu_row(i) for i in range(1, count + 1)], **kwargs)

    def us(self, count=2, rows=None, **kwargs):
        return write_clone(self.root, "prod-clone-us", 257, rows or [us_row(i) for i in range(1, count + 1)], **kwargs)

    def render(self, **kwargs):
        return dashboard.render_inventory(env="prod-clone", top=dashboard.PRODUCTION_GATE_NOTE, **kwargs)


class MergeTests(RegionsCase):
    def test_all_is_one_table_with_per_region_rows_and_counts(self):
        self.eu(3)
        self.us(2)
        for region in ("", "all", "ALL", "nonsense", "<b>x</b>"):
            page = self.render(region=region)
            self.assertEqual(sorted(row_regions(page)), ["EU", "EU", "EU", "US", "US"], region)
            self.assertIn("<b>5</b> of 5 tenants (3 EU + 2 US)", page)
            self.assertIn(">All <span class='n'>5</span></a>", page)
            self.assertIn(">EU <span class='n'>3</span></a>", page)
            self.assertIn(">US <span class='n'>2</span></a>", page)
            self.assertIn("aria-sort", page)
            self.assertIn(">Region</a></span></th>", page)  # sort by region from the Tenant header
            # one short line per region; its other facts sit in the Details block
            self.assertIn("<p class='tm-line'><b>EU</b> · 3 tenants · data from 2026-10-17 11:30 UTC</p>", page)
            self.assertIn("<p class='tm-line'><b>US</b> · 2 tenants · data from 2026-10-17 11:30 UTC</p>", page)
            details = re.search(r"<details class='tm-more tm-src'>.*?</details>", page, re.S).group(0)
            for text in ("PROD (cloned copy via Redash)", "PROD US (cloned copy via Redash)", "3 tenants (0 deleted, 0 without UUID)",
                         "2 tenants (0 deleted, 0 without UUID)", "<dt>Schema changes</dt><dd>0</dd>",
                         "<dt>Saved query</dt><dd>253</dd>", "<dt>Saved query</dt><dd>257</dd>"):
                self.assertIn(text, details)
            self.assertEqual(details.count("Data as of"), 2)
            self.assertNotIn("tm-stale'", page)
            self.assertNotIn("clone unavailable", page)
            self.assertNotIn("<b>x</b>", page)
        self.assertNotIn("href='/co/", page)  # production tenants never get dashboard CO links (as before)
        self.assertEqual([cells(row)[5] for row in body_rows(page)], ["<span class='muted'>—</span>"] * 5)

    def test_footer_names_both_configured_queries(self):
        self.eu(1)
        page = self.render()
        self.assertIn("<p class='tm-foot'>Sources: EU (saved query 253 on Prod (Cloned) - Mgmt) and US (saved query 257 on "
                      "Prod US (Cloned) - Mgmt).", page)
        self.assertNotIn("saved query 251", page)
        self.assertEqual(redash.region_query_id("EU"), 253)  # the note reads the profiles, never a hardcoded id
        with unpinned():
            self.assertIn("EU (saved query 253 on Prod (Cloned) - Mgmt) and US (not configured)", self.render())

    def test_eu_and_us_filters(self):
        self.eu(3)
        self.us(2)
        eu_page = self.render(region="eu")
        self.assertEqual(row_regions(eu_page), ["EU"] * 3)
        self.assertNotIn("US Tenant", eu_page)
        self.assertIn("<b>3</b> of 3 tenants</span>", eu_page)
        self.assertNotIn("<b>US</b> ·", eu_page)  # only the shown region's line
        self.assertNotIn("PROD US (cloned copy via Redash)", eu_page)
        self.assertNotIn(">Region</a>", eu_page)  # one region: no region sort
        us_page = self.render(region="US")
        self.assertEqual(row_regions(us_page), ["US"] * 2)
        self.assertEqual(row_names(us_page), ["US Tenant 1", "US Tenant 2"])
        self.assertIn("<b>2</b> of 2 tenants</span>", us_page)
        self.assertIn("<a class='on' aria-current='true' href='/prod/tenants?sort=name&amp;dir=asc&amp;region=us'>US "
                      "<span class='n'>2</span></a>", us_page)
        self.assertIn("<input type='hidden' name='region' value='us'>", us_page)
        self.assertIn(">EU <span class='n'>3</span></a>", us_page)  # the other region stays one click away

    def test_the_same_uuid_and_name_in_both_regions_is_two_rows(self):
        self.eu(2)
        twin = eu_row(1)  # identical _id, account UUID and name in the US clone
        self.us(rows=[us_row(1, _id=twin["_id"], accountUuid=twin["accountUuid"], accountName=twin["accountName"])])
        page = self.render()
        self.assertEqual(row_names(page).count("Example Tenant 1"), 2)
        self.assertEqual(page.count("<code>" + twin["_id"] + "</code>"), 2)
        self.assertEqual(sorted(row_regions(page)), ["EU", "EU", "US"])
        self.assertIn("<b>3</b> of 3 tenants (2 EU + 1 US)", page)


class MinimalLayoutTests(RegionsCase):
    """The minimal layout: title + subtitle, one line per region, Details, one filter row, 6 columns, per-row details."""

    def test_title_subtitle_one_search_box_and_one_filter_row(self):
        self.eu(2)
        self.us(1)
        page = self.render()
        self.assertIn("<div class='tenants-min'><div class='tm-head'><h1>Tenants</h1><span class='tm-sub'>Production · Redash "
                      "clone</span></div>", page)
        self.assertEqual(page.count("<input name='q'"), 1)
        self.assertEqual(page.count("class='tm-filters'"), 1)
        for label in (">All</a>", ">Completed</a>", ">Running</a>", ">Incomplete</a>", ">No status</a>", ">Any</a>",
                      ">With CO</a>", ">No CO</a>"):
            self.assertIn(label, page)
        body = page[page.index("<div class='tenants-min'>"):]
        self.assertNotIn("class='chip", body)  # no chips in the page body any more
        self.assertNotIn("<script", page.lower())
        self.assertIn("Every onboarding Start checks this production list first; a match stops it.", page)  # the gate note

    def test_the_table_has_six_columns_only(self):
        self.eu(2)
        self.us(1)
        for region in ("", "eu", "us"):
            page = self.render(region=region)
            head = re.search(r"<thead>(.*?)</thead>", page, re.S).group(1)
            labels = [re.sub(r"<[^>]+>", "", th).split(" · ")[0].rstrip(" ▲▼")
                      for th in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]
            self.assertEqual(labels, ["Tenant", "Domain", "Licence end", "Last scan", "Primary user", "CO"], region)
            self.assertTrue(body_rows(page))
            for row in body_rows(page):
                self.assertEqual(len(cells(row)), 6)

    def test_row_cells_and_details(self):
        write_clone(self.root, "prod-clone", 253, [eu_row(1, accountDomain="main.example.com",
                                                          alternateDomains=["alt1.example.com", "alt2.example.com"])])
        page = self.render(region="eu")
        tenant, domain, end, last_scan, primary, co_cell = cells(body_rows(page)[0])
        self.assertIn("<span class='tm-name'>Example Tenant 1</span>", tenant)
        self.assertIn("<span class='tm-meta'>EU · Trial</span>", tenant)
        self.assertIn("main.example.com <span class='muted'>+2</span>", domain)
        self.assertNotIn("alt1.example.com", domain)
        self.assertRegex(end, r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("2026-10-04<span class='tm-status'><i class='tm-dot ok' aria-hidden='true'></i>Completed</span>", last_scan)
        self.assertIn("✓", primary)
        self.assertEqual(co_cell, "<span class='muted'>—</span>")
        details = re.search(r"<details class='tm-more'><summary>Details</summary><dl>(.*?)</dl></details>", tenant, re.S).group(1)
        facts = dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", details, re.S))
        self.assertEqual(list(facts), ["Tenant ID", "Quota", "Alternate domains", "Scan interval", "SpyCloud", "Account",
                                       "Licence start", "Last scan duration"])
        self.assertEqual(facts["Tenant ID"], "<code>" + eu_row(1)["_id"] + "</code>")
        self.assertIn("10 / 2 / 50", facts["Quota"])
        self.assertEqual(facts["Alternate domains"], "alt1.example.com, alt2.example.com")
        self.assertEqual(facts["Scan interval"], "weekly")
        self.assertEqual(facts["SpyCloud"], "ON")
        self.assertEqual(facts["Account"], "enabled")
        self.assertEqual(facts["Licence start"], "2026-10-01")
        self.assertEqual(facts["Last scan duration"], "1 h 00 m")
        self.assertNotIn(eu_row(1)["accountUuid"], page)  # no more identifiers than before: the account UUID is not shown

    def test_scan_status_dot(self):
        write_clone(self.root, "prod-clone", 253, [eu_row(1, lastScanStatusEnum="COMPLETED"),
                                                    eu_row(2, lastScanStatusEnum="RUNNING"),
                                                    eu_row(3, lastScanStatusEnum="INCOMPLETE"),
                                                    eu_row(4, lastScanStatusEnum=None)])
        page = self.render(region="eu")
        rows = body_rows(page)
        dots = [re.search(r"<i class='tm-dot (\w+)'", cells(row)[3]).group(1) for row in rows]
        self.assertEqual(dots, ["ok", "warn", "bad", "none"])
        self.assertIn("></i>no status</span>", cells(rows[3])[3])

    def test_primary_user_cell_states(self):
        write_clone(self.root, "prod-clone", 253, [eu_row(1, primaryUserPresent=True), eu_row(2, primaryUserPresent=False),
                                                    eu_row(3, primaryUserPresent=None)])
        page = self.render(region="eu")
        primary = [cells(row)[4] for row in body_rows(page)]
        self.assertIn(">✓</span>", primary[0])
        self.assertEqual(primary[1], "<span class='tm-warn'>missing</span>")
        self.assertIn(">—</span>", primary[2])  # unknown is never "missing"
        self.assertNotIn("missing", primary[2])
        # a v1 tenant (no primaryUser column) is unknown, not missing
        self.assertEqual(dashboard._tm_primary_user({"primary_user": {"present": False}})[1], "")  # noqa: SLF001
        ordered = self.render(region="eu", sort="primary")
        self.assertEqual(row_names(ordered), ["Example Tenant 2", "Example Tenant 1", "Example Tenant 3"])  # unknown last

    def test_header_is_one_line_per_region_and_stale_is_one_word(self):
        self.eu(2, age=timedelta(hours=7))
        self.us(1)
        page = self.render()
        lines = re.findall(r"<p class='tm-line'>.*?</p>", page)
        self.assertEqual(len(lines), 2)
        self.assertIn("<b>EU</b> · 2 tenants · data from 2026-10-17 05:00 UTC <span class='tm-stale'", lines[0])
        self.assertTrue(lines[0].endswith(">stale</span></p>"))
        self.assertNotIn("tm-stale", lines[1])
        self.assertIn("stale, check that the collector is running", page)  # the full hint sits in Details
        self.assertEqual(page.count("<details class='tm-more tm-src'>"), 1)

    def test_number_and_time_formats(self):
        self.assertEqual(dashboard._tm_count(5473), "5,473")  # noqa: SLF001
        self.assertEqual(dashboard._tm_time("2026-11-10T17:35:00Z"), "2026-11-10 17:35 UTC")  # noqa: SLF001
        self.assertEqual(dashboard._tm_time("<b>"), "&lt;b&gt;")  # noqa: SLF001
        self.assertEqual(dashboard._tm_time(None), "—")  # noqa: SLF001

    def test_css_is_scoped_and_has_no_assets(self):
        css = dashboard.PENTERA_CSS[dashboard.PENTERA_CSS.index(".tenants-min{"):dashboard.PENTERA_CSS.index(".tm-foot{")]
        self.assertNotIn("url(", css)
        self.assertNotIn("@import", css)


class FailClosedTests(RegionsCase):
    def assert_us_unavailable(self, reason: str):
        page = self.render()
        self.assertEqual(row_regions(page), ["EU"] * 3)  # All still shows EU
        self.assertIn("US clone unavailable: " + reason + "</strong>", page)
        self.assertIn("this does not mean the region has no tenants", page)
        self.assertIn("(3 EU + US unavailable)", page)
        self.assertIn(">US <span class='n'>unavailable</span></a>", page)
        self.assertIn(">All</a>", page)  # no total while a region is missing
        self.assertNotIn("US <span class='n'>0", page)
        self.assertNotIn("<b>US</b> ·", page)
        us_page = self.render(region="us")
        self.assertIn("US clone unavailable: " + reason + "</strong>", us_page)
        self.assertNotIn("<table", us_page)  # the note replaces the rows
        self.assertNotIn("0 tenants", us_page)
        self.assertNotIn("of 0", us_page)
        self.assertIn(">EU <span class='n'>3</span></a>", us_page)
        return page, us_page

    def test_us_missing(self):
        self.eu(3)
        _, us_page = self.assert_us_unavailable("inventory_snapshot_missing")
        self.assertIn("<code>tools/redash_inventory_collector.py --collect --region us</code>", us_page)

    def test_us_stale_query(self):
        self.eu(3)
        self.us(2, age=timedelta(hours=7))
        page, _ = self.assert_us_unavailable("query_stale")
        self.assertNotIn("US Tenant", page)  # stale US rows are never shown

    def test_us_stale_data(self):
        self.eu(3)
        self.us(rows=[us_row(1, _modified="2026-10-01T00:00:00.000000")])
        self.assert_us_unavailable("data_stale")

    def test_us_not_configured(self):
        self.eu(3)
        self.us(2)  # a US snapshot on disk is still not shown once the US query is switched off
        with unpinned():
            page, _ = self.assert_us_unavailable("region_not_configured")
        self.assertNotIn("US Tenant", page)

    def test_us_wrong_region(self):
        self.eu(3)
        # an EU-query snapshot written into the US folder: environment says US, the source query says EU
        normalized = redash.normalize(v2.reply([us_row(1)], (NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")), 253)
        payload = inventory.snapshot_payload(
            inventory.require_environment("prod-clone-us"), inventory.assemble_rows(normalized.rows), normalized.retrieved_at,
            acquisition=inventory.REDASH_ACQUISITION, source=redash.source_meta(normalized, NOW, refreshed=False),
            expected_paths=redash.expected_paths(253))
        inventory.write_snapshot(redash.attach_v2(payload, normalized), self.root, keep=4)
        self.assert_us_unavailable("inventory_snapshot_wrong_region")

    def test_us_unreadable_never_breaks_the_page(self):
        self.eu(3)
        with patch.object(dashboard.production_views, "clone_status", side_effect=OSError("disk")):
            self.assert_us_unavailable("inventory_snapshot_unreadable")

    def test_eu_missing(self):
        self.us(2)
        page = self.render()
        self.assertEqual(row_regions(page), ["US", "US"])
        self.assertIn("EU clone unavailable: inventory_snapshot_missing</strong>", page)
        self.assertIn("redash_inventory_collector.py --collect", page)
        self.assertIn("PROD US (cloned copy via Redash)", page)
        self.assertIn("(EU unavailable + 2 US)", page)
        self.assertIn(">EU <span class='n'>unavailable</span></a>", page)
        eu_page = self.render(region="eu")
        self.assertIn("EU clone unavailable: inventory_snapshot_missing</strong>", eu_page)
        self.assertNotIn("<table", eu_page)

    def test_eu_stale_keeps_todays_warning_rows(self):
        self.eu(3, age=timedelta(hours=7))
        self.us(2)
        page = self.render()
        self.assertEqual(sorted(row_regions(page)), ["EU", "EU", "EU", "US", "US"])
        self.assertIn(">stale</span>", page)
        self.assertIn("stale, check that the collector is running", page)

    def test_eu_wrong_region(self):
        write_clone(self.root, "prod-clone", 253, [eu_row(1)])
        self.us(2)
        with patch.object(redash, "snapshot_region", side_effect=lambda payload: "US"):
            page = self.render()
        self.assertIn("EU clone unavailable: inventory_snapshot_wrong_region</strong>", page)
        self.assertNotIn("Example Tenant", page)

    def test_both_missing_shows_two_notes_and_no_table(self):
        page = self.render()
        self.assertIn("EU clone unavailable: inventory_snapshot_missing", page)
        self.assertIn("US clone unavailable: inventory_snapshot_missing", page)
        self.assertNotIn("<table", page)
        self.assertIn(">EU <span class='n'>unavailable</span></a>", page)
        self.assertIn(">US <span class='n'>unavailable</span></a>", page)
        self.assertNotIn("0 tenants", page)
        self.assertNotIn("<span class='n'>0", page)

    def test_vm_without_a_us_clone_shows_the_note_not_an_error(self):
        self.eu(3)
        with patch.object(dashboard, "vm_mode", return_value=True):
            page = self.render()
            us_page = self.render(region="us")
        self.assertEqual(row_regions(page), ["EU"] * 3)
        for each in (page, us_page):
            self.assertIn("US clone unavailable: inventory_snapshot_missing", each)
            self.assertIn("This server does not collect the US clone yet.", each)
            self.assertNotIn("redash_inventory_collector.py --collect --region us</code>", each)


class ParameterTests(RegionsCase):
    def test_filters_search_and_sort_keep_the_region(self):
        self.eu(3)
        self.us(3, rows=[us_row(1), us_row(2, lastScanStatusEnum="RUNNING"), us_row(3)])
        page = self.render(query="Tenant", sort="scan", direction="desc", scan="COMPLETED", co="none", region="us")
        self.assertEqual(sorted(row_names(page)), ["US Tenant 1", "US Tenant 3"])
        self.assertIn("<input type='hidden' name='region' value='us'>", page)
        links = re.findall(r"href='(/prod/tenants\?[^']*)'>([^<]*)", page)
        self.assertTrue(links)
        for href, label in links:
            self.assertIn("q=Tenant", href)
            if label in ("All ", "EU "):  # only the other region options change the region (the scan "All" keeps it)
                self.assertNotIn("region=us", href)
            else:
                self.assertIn("region=us", href, label)
        self.assertIn("href='/prod/tenants?q=Tenant&amp;sort=scan&amp;dir=asc&amp;scan=COMPLETED&amp;co=none&amp;region=us'",
                      page)  # the active column toggles direction and keeps the region

    def test_sort_by_region(self):
        self.eu(2)
        self.us(2)
        ascending = self.render(sort="region")
        self.assertEqual(row_regions(ascending), ["EU", "EU", "US", "US"])
        descending = self.render(sort="region", direction="desc")
        self.assertEqual(row_regions(descending), ["US", "US", "EU", "EU"])
        self.assertEqual(row_names(descending), ["US Tenant 2", "US Tenant 1", "Example Tenant 2", "Example Tenant 1"])  # ties by name, reversed as on every column
        # the Dev page has no region: its sort falls back to name and it has no Region column
        self.assertNotIn(">Region</a>", dashboard.render_inventory(env="dev", sort="region"))

    def test_sort_by_each_visible_column(self):
        self.eu(3)
        page = self.render(region="eu")
        for key in ("name", "domain", "end", "scan", "primary", "co"):
            self.assertIn("sort=" + key, page)
            sorted_page = self.render(region="eu", sort=key, direction="desc")
            self.assertEqual(len(body_rows(sorted_page)), 3, key)
            self.assertIn("aria-sort='descending'", sorted_page)

    def test_search_across_both_regions(self):
        self.eu(2)
        self.us(2)
        page = self.render(query="tenant 2")
        self.assertEqual(sorted(row_names(page)), ["Example Tenant 2", "US Tenant 2"])
        self.assertIn("<b>2</b> of 4 tenants (2 EU + 2 US)", page)

    def test_no_match_says_so(self):
        self.eu(2)
        page = self.render(co="linked")  # production tenants have no dashboard CO link
        self.assertEqual(body_rows(page), [])
        self.assertIn("No tenant matches these filters.", page)

    def test_route_passes_region_and_old_urls_keep_it(self):
        self.eu(1)
        self.us(1)

        class Request:
            def __init__(self, path):
                self.path, self.redirects, self.pages, self.headers = path, [], [], {}

            def send_redirect(self, location):
                self.redirects.append(location)

            def send_page(self, status, page):
                self.pages.append((int(status), page))

        with patch.object(dashboard, "login_required", return_value=False), \
                patch.object(dashboard, "request_origin_problem", return_value=None):
            us = Request("/prod/tenants?region=us&q=US")
            dashboard.Handler.do_GET(us)
            old = Request("/tenants?region=eu&q=acme")
            dashboard.Handler.do_GET(old)
            dev = Request("/inventory?region=us")
            dashboard.Handler.do_GET(dev)
        self.assertEqual(us.pages[0][0], 200)
        self.assertEqual(row_regions(us.pages[0][1]), ["US"])
        self.assertEqual(old.redirects, ["/prod/tenants?region=eu&q=acme"])
        self.assertNotIn(">Region</a>", dev.pages[0][1])


class SafetyTests(RegionsCase):
    def test_values_are_escaped_and_the_page_is_read_only(self):
        self.eu(1)
        self.us(rows=[us_row(1, accountName="<script>alert(1)</script>", accountDomain="x.example.com")])
        page = self.render(query="<img src=x>", region="all")
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<img src=x>", page)
        page = self.render()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<script", page.lower())
        self.assertEqual(set(re.findall(r"<form[^>]*method='(\w+)'", page)), {"get"})
        self.assertNotIn("/attended/", page.replace("/attended/inventory-refresh", "!"))
        self.assertNotIn("inventory-refresh", page)

    def test_row_details_are_escaped(self):
        payload = {"id": "<b>id</b>", "account_uuid": None, "alternate_domains": ["<i>a</i>.example.com"],
                   "scanning_interval": "<u>x</u>", "spycloud_enabled": None}
        html = dashboard._tm_row_details(payload, {"type": "<s>t</s>"}, {}, {"status": "<b>s</b>", "at": "2026-11-10T10:00:00Z"})  # noqa: SLF001
        for raw in ("<b>id</b>", "<i>a</i>", "<u>x</u>", "<b>s</b>"):
            self.assertNotIn(raw, html)
        self.assertIn("no UUID", html)

    def test_unavailable_reason_is_escaped(self):
        self.eu(1)
        with patch.object(dashboard.production_views, "clone_status",
                          return_value={"state": "unusable", "reason": "<b>bad</b>", "captured_at": "<i>t</i>"}):
            page = self.render()
        self.assertIn("US clone unavailable: &lt;b&gt;bad&lt;/b&gt;", page)
        self.assertIn("last data as of &lt;i&gt;t&lt;/i&gt;", page)
        self.assertNotIn("<b>bad</b>", page)


if __name__ == "__main__":
    unittest.main()
