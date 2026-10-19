"""Production-first dashboard (owner-approved design docs/46 + data contract docs/47).

Hermetic: every Salesforce, clone and Leonardo reader is stubbed (a real call would fail the test); approvals, create
records and the clone live in a temp folder. Production pages are read-only: while the runner is locked no page
carries a create, Start or Confirm control (the HTML is scanned), and nothing here can reach a production write.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from integration.onboarding import production_approval as pa
from integration.onboarding import production_create_record as pcr
from integration.onboarding import session_readiness as readiness
from integration.tests import test_dev_prod_areas as areas_module
from integration.tests.test_redash_inventory import FAKE_KEY, GET, FakeOpener, result, row as clone_row
import tools.redash_inventory_collector as collector
import tools.serve_attended_open_onboardings_dashboard as dashboard

# Both create constants True, patched explicitly so tests of the unlocked pages keep proving them after the relock commit.
unlocked_build = patch.multiple(readiness, PRODUCTION_RUNNER_SUPPORTED=True, PRODUCTION_DRY_RUN_SUPPORTED=True)
# The create lock patched back to False inside an unlocked_build test (the real build is locked).
locked_create_build = patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False)

CE = {"Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT, "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE}
ROWS = [
    {"Name": "CO-0780", "Account__r.Name": "State Land Service", **CE, "Submission_Date__c": "2026-10-10"},
    {"Name": "CO-0781", "Account__r.Name": "Demo Logistics", **CE, "Submission_Date__c": "2026-10-11"},
    {"Name": "CO-0782", "Account__r.Name": "Two Live Paid", **CE, "Submission_Date__c": "2026-10-12"},
    {"Name": "CO-0783", "Account__r.Name": "Manual Renewal", "Onboarding_Product__c": "Credential Exposure",
     "Onboarding_Type__c": "Renewal of Existing Product", "Submission_Date__c": "2026-10-13"},
    {"Name": "CO-0784", "Account__r.Name": "Stale Clone Co", **CE, "Submission_Date__c": "2026-10-14"},
    {"Name": "CO-0785", "Account__r.Name": "Already Created", **CE, "Submission_Date__c": "2026-10-15"},
]
STARTS = {"CO-0780": "2026-10-18", "CO-0781": "2026-11-20", "CO-0782": "2026-10-30", "CO-0784": "2026-10-25",
          "CO-0785": "2026-10-17"}
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
CAPTURED = datetime(2026, 10, 16, 9, 50, tzinfo=timezone.utc)  # 2 h 10 min before NOW


def match(status="not_in_production", reason="", candidates=(), **extra):
    return {"status": status, "reason": reason, "fields": [], "differs": [], "candidates": list(candidates), "target": None,
            "captured_at": "2026-10-16T09:50:00Z", "age_seconds": 7800, "tenants_checked": 1204, "route": None, **extra}


LIVE = {"id": "t1", "name": "Two Live Paid", "domain": "live.example", "live_paid": True, "deleted": False, "enabled": True,
        "license_type": "PREPAID_ANNUAL"}
MATCHES = {
    "CO-0780": match(), "CO-0785": match(),
    "CO-0781": match("exists_matches", candidates=[{**LIVE, "name": "Demo Logistics"}]),
    "CO-0782": match("ambiguous", candidates=[LIVE, {**LIVE, "id": "t2"}]),
    "CO-0784": match("clone_unavailable", reason="inventory_snapshot_stale", age_seconds=None, captured_at=None),
}


def approval(co="CO-0780", ref="PROD-CE-0780-1", issued="2026-10-16T09:00:00Z", expires="2026-10-23T09:00:00Z", **extra):
    return {"approval_ref": ref, "co_reference": co, "route": pa.ALLOWED_ROUTE, "scope": pa.SCOPE,
            "source_revision": "SECRET-REVISION-9", "issued_by": "owner@example.test", "issued_at": issued,
            "expires_at": expires, **extra}


def record(co="CO-0785", outcome="readback_verified", **extra):
    return {"co_reference": co, "route": "case_2_new_ce_only", "approval_ref": "PROD-CE-0785-1", "outcome": outcome,
            "tenant_name": "Already Created", "domains": ["created.example"], "account_id": "a" * 24, "account_uuid": "b" * 32,
            "plan_summary": "CE, 1 year", "salesforce_revision": "SECRET-REVISION-9", **extra}


UNSET = object()


class Env:
    """Temp folder with a clone, create approvals, create records and summary approvals."""

    def __init__(self, clone=True, now=NOW):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.clone_root = self.dir / "inventory"
        self.parent = self.dir / "state"
        self.parent.mkdir()
        self.approvals = self.dir / "production_create_approvals.json"
        self.summary_approvals = self.dir / "onboarding_review_approvals.json"
        self.now = now
        if clone:
            collector.collect(FakeOpener({GET: result([clone_row(1)], "2026-10-16T09:50:00")}), FAKE_KEY, root=self.clone_root,
                              now=CAPTURED + timedelta(minutes=5))

    def write_approvals(self, *records):
        self.approvals.write_text(json.dumps({item["approval_ref"]: item for item in records}), encoding="utf-8")

    def write_record(self, data=None, moment=NOW):
        return pcr.record(self.parent, data or record(), moment)


@contextlib.contextmanager
def patched(env, rows=ROWS, matches=MATCHES, starts=STARTS, unavailable=False, unlock=UNSET):
    dashboard.clear_display_cache()
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(patch.object(dashboard, "queue_rows", side_effect=lambda: [dict(item) for item in rows]))
        enter(patch.object(dashboard, "queue_start_dates", return_value=dict(starts)))
        enter(patch.object(dashboard, "prefetch_display_read"))
        enter(patch.object(dashboard, "UNSTAMPED_MATCH_ALLOWED", True))  # canned matches carry no computed_at
        enter(patch.object(dashboard, "production_match_for",
                           side_effect=lambda ref, product=None, kind=None: matches.get(ref, match())))
        enter(patch.object(dashboard, "inventory_root", return_value=env.clone_root))
        enter(patch.object(dashboard, "PRODUCTION_APPROVAL_PATH", env.approvals))
        enter(patch.object(dashboard, "REVIEW_APPROVAL_PATH", env.summary_approvals))
        enter(patch.object(dashboard, "production_records_parent", return_value=env.parent))
        enter(patch.object(dashboard, "production_now", return_value=env.now))
        enter(patch.object(dashboard, "_state_not_published", return_value=unavailable))
        enter(patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce in production dashboard tests")))
        if unlock is not UNSET:
            enter(patch.object(readiness, "production_unlock_problem", return_value=unlock))
        yield
    dashboard.clear_display_cache()


def page_of(path, env, **kwargs):
    with patched(env, **kwargs):
        request = areas_module.get(path)
    assert request.pages, (path, request.redirects)
    return request.pages[0]


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def headers(table_html):
    return [text(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", table_html, flags=re.S)]


ALLOWED_FORM_ACTIONS = ("/search",)
CREATE_WORDS = re.compile(r"create|start|confirm|submit|\bgo\b|run |dry run|launch|onboard in|production write", re.I)


def controls(page, allow=()):
    """Every interactive control in the page body that is not on the allow list, as (kind, label/action)."""
    found = []
    for form in re.findall(r"<form[^>]*>", page, flags=re.I):
        action = re.search(r"action='([^']*)'", form)
        if action and action.group(1) in ALLOWED_FORM_ACTIONS + tuple(allow):
            continue
        found.append(("form", form))
    for button in re.findall(r"<button[^>]*>.*?</button>", page, flags=re.I | re.S):
        label = text(button)
        if label in ("Search",) or label in allow:
            continue
        found.append(("button", label))
    for field in re.findall(r"<input[^>]*>", page, flags=re.I):
        if "name='q'" in field or ("type='hidden'" in field and allow):
            continue
        found.append(("input", field))
    for link in re.findall(r"<a[^>]*class='btn[^']*'[^>]*>.*?</a>", page, flags=re.S):
        found.append(("btn-link", link))
    if re.search(r"<script", page, flags=re.I):
        found.append(("script", ""))
    return found


class NavigationTests(unittest.TestCase):
    PRODUCTION = [("/", "Onboarding"), ("/prod/tenants", "Tenants"), ("/history", "History"), ("/prod/approvals", "Approvals")]
    TOOLS = [("/connection", "Sessions")]
    DEV = [("/dev", "Onboardings"), ("/dev/onboarded", "Onboarded on Dev"), ("/inventory", "Dev tenants")]

    def aside(self, active="prod_onboarding"):
        shell = dashboard._app_shell("t", "<p>body</p>", active=active)
        return shell[shell.index("<aside"):shell.index("</aside>")]

    def test_groups_order_and_labels(self):
        aside = self.aside()
        links = re.findall(r"<a href='([^']+)'(?: class='active')?>([^<]+)</a>", aside)
        self.assertEqual(links, self.PRODUCTION + self.TOOLS + self.DEV)
        heads = [re.sub(r"<[^>]+>", "", head) for head in re.findall(r"<div class='navhead'>(.*?)</div>", aside)]
        self.assertEqual(heads, ["PRODUCTIONread-only", "TOOLS"])
        self.assertIn("<summary>DEV - LEONARDO</summary>", aside)
        self.assertLess(aside.index("navgroup prod"), aside.index("navgroup tools"))
        self.assertLess(aside.index("navgroup tools"), aside.index("navgroup dev"))
        self.assertLess(aside.index("navgroup tools"), aside.index("DEV - LEONARDO"))

    def test_no_matches_readiness_or_records_menu_items(self):
        aside = self.aside()
        for label in ("Matches", "Readiness", "Records", "ONBOARDING"):
            self.assertNotIn(">" + label + "<", aside)
        self.assertNotIn("/prod/matches", aside)
        self.assertNotIn("href='/prod'", aside)

    def test_dev_group_is_collapsed_unless_a_dev_page_is_active(self):
        self.assertRegex(self.aside("prod_onboarding"), r"<details class='navgroup dev'>")
        self.assertRegex(self.aside("history"), r"<details class='navgroup dev'>")
        for active in ("onboardings", "dev_onboarded", "inventory"):
            self.assertRegex(self.aside(active), r"<details class='navgroup dev' open>", active)

    def test_one_active_link_and_the_right_environment_per_page(self):
        expected = {"prod_onboarding": "prod", "prod_tenants": "prod", "history": "prod", "prod_approvals": "prod",
                    "connection": "tools", "onboardings": "dev", "dev_onboarded": "dev", "inventory": "dev"}
        pills = {"dev": "DEV · Leonardo", "prod": "PRODUCTION · read-only", "tools": "TOOLS"}
        for active, env in expected.items():
            shell = dashboard._app_shell("t", "<p>body</p>", active=active)
            self.assertEqual(shell.count("class='active'"), 1, active)
            self.assertIn("<div class='envbar " + env + "' aria-hidden='true'></div>", shell, active)
            self.assertIn("<span class='envpill " + env + "'>" + pills[env] + "</span>", shell, active)

    def test_the_dev_group_keeps_its_navy_pill_and_the_tokens_are_reused(self):
        css = dashboard.PENTERA_CSS
        self.assertIn("details.navgroup.dev>summary", css)
        self.assertIn("--env-prod:var(--warn);--env-prod-bg:var(--warn-bg)", css)
        self.assertIn(".navgroup.dev a::before{background", css)
        self.assertNotIn("#d92d20", css[css.index("details.navgroup.dev>summary"):][:400])


class LandingTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.tmp.cleanup)
        self.env.write_approvals(approval())
        self.env.write_record()

    def page(self, path="/", **kwargs):
        status, html = page_of(path, self.env, **kwargs)
        self.assertEqual(status, 200)
        return html

    def row_of(self, page, ref):
        start = page.index(">" + ref + "</a>") if ">" + ref + "</a>" in page else page.index(">" + ref + "<")
        return page[page.rindex("<tr", 0, start):page.index("</tr>", start)]

    def test_columns_in_the_owner_order(self):
        page = self.page()
        table = page[page.index("<table"):page.index("</table>")]
        self.assertEqual(headers(table), ["Start", "Onboarding", "Account", "Product", "Production checks", "Approval create · 7 days",
                                          "Create", "Age"])
        self.assertIn("<h1>Onboarding</h1>", page)
        self.assertIn("envpill prod", page)
        self.assertNotIn("envpill dev", page)

    def test_root_and_onboard_alias_are_the_same_production_page(self):
        self.assertEqual(self.page("/"), self.page("/onboard"))
        self.assertEqual(self.page("/onboard?filter=blocked"), self.page("/?filter=blocked"))

    def test_the_dev_queue_moved_to_dev_and_stays_the_dev_page(self):
        with areas_module.areas(), patch.object(dashboard, "queue_rows", return_value=[dict(r) for r in areas_module.ROWS]), \
                patch.object(dashboard, "queue_start_dates", return_value={}), \
                patch.object(dashboard, "closed_history", return_value=None), patch.object(dashboard, "warm_co_pages"):
            request = areas_module.get("/dev")
            page = request.pages[0][1]
            filtered = areas_module.get("/dev?queue=ready&sort=co").pages[0][1]
        self.assertEqual(request.pages[0][0], 200)
        self.assertIn("envpill dev", page)
        self.assertIn("5 onboarded on Dev", page)
        self.assertIn("href='/dev?queue=", page)
        self.assertIn("action='/dev'", page)
        self.assertNotIn("href='/?queue=", page)
        self.assertIn("href='/dev?", filtered)

    def test_rows_chips_and_links(self):
        page = self.page()
        ok = self.row_of(page, "CO-0780")
        self.assertIn("href='/onboard/CO-0780'", ok)
        self.assertIn("<span class='chip chip-ok'>Clear</span>", ok)
        self.assertIn("<span class='chip chip-ok'>Valid to 10-23</span>", ok)
        self.assertIn("<span class='chip chip-warn'>Locked</span>", ok)
        self.assertIn("Oct 18", ok)
        self.assertIn("window open", ok)
        self.assertIn("State Land Service", ok)
        self.assertIn("Credential Exposure", ok)
        dup = self.row_of(page, "CO-0781")
        self.assertIn("<span class='chip chip-bad'>Duplicate</span>", dup)
        self.assertIn("<span class='chip chip-neutral'>None</span>", dup)
        self.assertIn("window opens 11-18", dup)
        self.assertIn("<span class='chip chip-bad'>Live paid x 2</span>", self.row_of(page, "CO-0782"))
        self.assertIn("<span class='chip chip-warn'>Clone stale</span>", self.row_of(page, "CO-0784"))
        created = self.row_of(page, "CO-0785")
        self.assertIn("<span class='chip chip-ok'>Verified</span>", created)
        manual = self.row_of(page, "CO-0783")
        self.assertIn("No automated route", manual)
        self.assertNotIn("href='/onboard/CO-0783'", page)
        self.assertIn("locked", manual)  # greyed

    def test_a_missing_match_is_never_a_clearance(self):
        class Boom(dict):
            def get(self, *_args):
                raise RuntimeError("boom")
        page = self.page(matches=Boom())
        self.assertNotIn("chip-ok'>Clear</span>", page)
        self.assertIn("<span class='chip chip-warn'>Clone unavailable</span>", page)

    def test_filters_with_counts(self):
        page = self.page()
        counts = {label: int(number) for label, number in re.findall(r"class='fchip(?: on)?' href='[^']+'>([A-Za-z ]+) <b>(\d+)</b>", page)}
        self.assertEqual(counts, {"All": 6, "Ready for dry run": 1, "Blocked": 3, "Approved": 1, "Created": 1})
        refs = lambda html: re.findall(r"<a class='co' href='/onboard/(CO-\d+)'>", html)  # noqa: E731
        self.assertEqual(sorted(refs(self.page("/?filter=blocked"))), ["CO-0781", "CO-0782", "CO-0784"])
        self.assertEqual(refs(self.page("/?filter=ready")), ["CO-0780"])
        self.assertEqual(refs(self.page("/?filter=approved")), ["CO-0780"])
        self.assertEqual(refs(self.page("/?filter=created")), ["CO-0785"])
        self.assertEqual(len(refs(self.page("/?filter=bogus"))), 5)

    def test_default_order_is_the_soonest_start_first(self):
        refs = re.findall(r"<a class='co' href='/onboard/(CO-\d+)'>", self.page())
        self.assertEqual(refs, ["CO-0785", "CO-0780", "CO-0784", "CO-0782", "CO-0781"])
        by_co = re.findall(r"<a class='co' href='/onboard/(CO-\d+)'>", self.page("/?sort=co&dir=desc"))
        self.assertEqual(by_co, ["CO-0785", "CO-0784", "CO-0782", "CO-0781", "CO-0780"])

    def test_clone_strip_trusted_stale_and_unusable(self):
        fresh = self.page()
        self.assertIn("<span class='chip chip-ok'>Clone 2 h 10 min old, limit 6 h &middot; trusted</span>", fresh)
        self.env.now = CAPTURED + timedelta(hours=6)
        self.assertIn("chip-ok'>Clone 6 h 0 min old, limit 6 h &middot; trusted", self.page())
        self.env.now = CAPTURED + timedelta(hours=6, seconds=1)
        stale = self.page()
        self.assertIn("<span class='chip chip-warn'>Clone 6 h 0 min old, limit 6 h &middot; stale, not trusted</span>", stale)
        gone = Env(clone=False)
        self.addCleanup(gone.tmp.cleanup)
        page = page_of("/", gone)[1]
        self.assertIn("<span class='chip chip-warn'>Clone unusable: inventory_snapshot_missing</span>", page)
        self.assertIn("redash_inventory_collector.py --collect", page)

    def test_lock_chip_while_locked(self):
        self.assertIn("<span class='chip chip-warn'>Create locked</span>", self.page())

    @unlocked_build
    def test_unlocked_build_has_no_lock_chip_even_without_an_approval_in_the_environment(self):
        # Owner feedback 2026-10-18: the session unlock comes from each CO's approval record, never os.environ.
        page = self.page()
        self.assertNotIn("Create locked", page)
        self.assertNotIn("<span class='chip chip-warn'>Locked</span>", self.row_of(page, "CO-0780"))

    def test_unreadable_or_unpublished_state_never_reads_as_none(self):
        self.env.approvals.write_text("garbage", encoding="utf-8")
        page = self.page()
        self.assertIn("<span class='chip chip-warn'>Unreadable</span>", self.row_of(page, "CO-0780"))
        self.assertNotIn("chip-neutral'>None</span>", self.row_of(page, "CO-0780"))
        self.env.approvals.unlink()
        shutil_dir = self.env.parent / pcr.RECORDS_SUBDIR
        for item in shutil_dir.iterdir():
            item.unlink()
        shutil_dir.rmdir()
        desktop = self.page()
        self.assertIn("<span class='chip chip-neutral'>None</span>", self.row_of(desktop, "CO-0780"))
        server = self.page(unavailable=True)
        row = self.row_of(server, "CO-0780")
        self.assertEqual(row.count("Unavailable on this server"), 2)  # approval and create
        self.assertNotIn("chip-neutral'>None</span>", row)
        self.assertNotIn("Locked", row)

    def test_corrupt_create_records_show_unreadable_not_none(self):
        folder = self.env.parent / pcr.RECORDS_SUBDIR
        (folder / "CO-0780-20261016T120000Z.json").write_text("garbage", encoding="utf-8")
        page = self.page()
        self.assertIn("<span class='chip chip-warn'>Records unreadable</span>", self.row_of(page, "CO-0780"))
        self.assertNotIn("chip-ok'>Verified", page)

    def test_a_queue_read_failure_is_the_existing_unavailable_page(self):
        with patched(self.env), patch.object(dashboard, "queue_rows", side_effect=dashboard.ReadUnavailable()), \
                patch.object(dashboard, "set_session_status"):
            request = areas_module.get("/")
        self.assertEqual(request.pages[0][0], 503)

    def test_missing_start_dates_are_shown_as_unavailable_not_invented(self):
        with patched(self.env), patch.object(dashboard, "queue_start_dates", side_effect=dashboard.ReadUnavailable()):
            request = areas_module.get("/")
        html = request.pages[0][1]
        self.assertEqual(request.pages[0][0], 200)
        self.assertIn("could not be read", html)

    def test_no_create_start_or_confirm_control_and_no_script(self):
        for path in ("/", "/onboard", "/?filter=approved", "/?filter=created"):
            self.assertEqual(controls(self.page(path)), [], path)

    def test_values_are_escaped(self):
        rows = [{**ROWS[0], "Account__r.Name": "<img src=x onerror=alert(1)>"}]
        page = self.page(rows=rows)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)

    def test_no_secrets_or_internals(self):
        page = self.page()
        for secret in ("SECRET-REVISION-9", "summary_hash", "source_revision"):
            self.assertNotIn(secret, page)


def summary_for(**over):
    base = {"reference": "CO-0780", "account": "State Land Service", "product": dashboard.CE_ROUTE_PRODUCT,
            "onboarding_type": dashboard.CE_ROUTE_TYPE, "route": dashboard.CE_ENGINE, "source_revision": "rev-1",
            "values": {"tenant_names": ["State Land Service"], "domains": ["vzd.example"],
                       "plan": {"license_type": "Credential Exposure", "license_start": "2026-10-18",
                                "license_end": "2027-10-17", "spycloud_shown": True}},
            "production_check": {"status": "not_in_production", "reason": "", "tenants_checked": 1204, "candidates": []},
            "leonardo_consulted": False, "blockers": []}
    base.update(over)
    return base


EXTRAS = {"captured_at": "2026-10-16T09:50:00Z", "age_seconds": 7800}
ROW = {"Name": "CO-0780", "Account_Name__c": "State Land Service", "Account_UUID__c": None, **CE}


class CoPageTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.tmp.cleanup)

    def page(self, summary=None, extras=None, row=ROW, notice="", **kwargs):
        with patched(self.env, **kwargs):
            return dashboard.page_onboard_review(summary or summary_for(), extras or EXTRAS, notice, row=row)

    def heads(self, page):
        return [text(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", page, flags=re.S)]

    def test_cards_in_the_production_first_order(self):
        page = self.page()
        self.assertEqual(self.heads(page), ["Production create", "Production readiness", "Salesforce facts", "Production checks",
                                            "Add Account values", "Approval", "D2 record"])
        self.assertIn("<a class='crumb' href='/'>", page)
        self.assertIn("envpill prod", page)

    def test_production_checks_card_has_the_duplicate_validation_rows(self):
        summary = summary_for(production_check={"status": "exists_other", "reason": "Only trial tenants match", "tenants_checked": 1204,
                                                 "candidates": [
                                                     {"name": "State Land Service", "domain": "other.example", "live_paid": True,
                                                      "deleted": False, "enabled": True, "license_type": "PREPAID_ANNUAL"},
                                                     {"name": "Other", "domain": "VZD.example", "live_paid": False,
                                                      "deleted": False, "enabled": True, "license_type": "Trial"}]},
                              blockers=["production_exists_other"])
        page = self.page(summary)
        card = page[page.index("<h2 class='pill'>Production checks"):page.index("<h2 class='pill'>Approval")]
        for label in ("Clone age", "Duplicate validation: production only", "Duplicate by name", "Duplicate by domain",
                      "Live paid tenants (exact name)", "Timing window", "Email domain", "Account UUID"):
            self.assertIn(label, card, label)
        by_name = card[card.index("Duplicate by name"):card.index("Duplicate by domain")]
        self.assertIn("chip-bad'>1 match</span>", by_name)
        by_domain = card[card.index("Duplicate by domain"):card.index("Live paid tenants")]
        self.assertIn("chip-bad'>1 match</span>", by_domain)
        live = card[card.index("Live paid tenants"):]
        self.assertIn("chip-warn'>1</span>", live)
        self.assertIn("exists other", card)
        self.assertIn("Only trial tenants match", card)
        self.assertIn("1204", card)

    def test_a_clear_check_and_an_unusable_clone(self):
        page = self.page()
        card = page[page.index("<h2 class='pill'>Production checks"):page.index("<h2 class='pill'>Approval")]
        self.assertIn("not in production", card)
        self.assertIn("chip-ok'>None</span>", card[card.index("Duplicate by name"):])
        self.assertIn("Captured ", card)
        self.assertIn("2 h 10 min", card)
        self.assertIn("chip-ok'>Fresh</span>", card)
        self.assertIn("A missing match is never a clearance.", card)
        gone = Env(clone=False)
        self.addCleanup(gone.tmp.cleanup)
        with patched(gone):
            unusable = dashboard.page_onboard_review(
                summary_for(production_check={"status": "clone_unavailable", "reason": "inventory_snapshot_missing",
                                              "tenants_checked": 0, "candidates": []}, blockers=["production_clone_unavailable"]),
                {"captured_at": None, "age_seconds": None}, "", row=ROW)
        card = unusable[unusable.index("<h2 class='pill'>Production checks"):unusable.index("<h2 class='pill'>Approval")]
        self.assertIn("chip-warn'>Unusable</span>", card)
        self.assertIn("Duplicate check unavailable", card)
        self.assertNotIn("chip-ok'>None</span>", card[card.index("Duplicate by name"):card.index("Timing window")])

    def test_timing_email_and_uuid_rows(self):
        page = self.page()
        card = page[page.index("<h2 class='pill'>Production checks"):page.index("<h2 class='pill'>Approval")]
        self.assertIn("chip-ok'>Open</span>", card)  # start 2026-10-18, window opens 10-16, today 10-16
        self.assertIn("chip-ok'>1</span>", card[card.index("Email domain"):])
        self.assertIn("chip-ok'>Empty</span>", card[card.index("Account UUID"):])
        closed = self.page(summary_for(values={**summary_for()["values"], "plan": {"license_start": "2026-11-20"}}))
        self.assertIn("chip-bad'>Closed</span>", closed)
        self.assertIn("opens 2026-11-18", closed)
        unknown = self.page(row=None)
        self.assertIn("Account UUID", unknown)
        self.assertNotIn("chip-ok'>Empty</span>", unknown)
        filled = self.page(row={**ROW, "Account_UUID__c": "a" * 32})
        self.assertIn("chip-bad'>Already set</span>", filled)
        self.assertNotIn("a" * 32, filled)

    def test_locked_build_names_the_gate_reason_once_and_has_no_create_control(self):
        page = self.page()
        self.assertNotIn("class='banner banner-warn' role='status'><strong>Production create is locked.", page)  # no banner
        create = page[page.index("<h2 id='co-create-title'"):page.index("</section>", page.index("<h2 id='co-create-title'"))]
        self.assertIn("chip-warn'>Locked</span>", create)
        self.assertIn(dashboard.create_ui.gate_text("production_runner_not_supported"), create)
        self.assertEqual(controls(page, allow=("/attended/review-approve", "Approve this summary")), [])
        self.assertIn("<span class='chip chip-warn'>Create locked</span>", page)

    def test_no_create_control_in_any_state_even_with_a_valid_approval_and_a_verified_record(self):
        self.env.write_approvals(approval())
        self.env.write_record(record("CO-0780", "readback_verified"))
        for state in ({}, {"unlock": "production_runner_not_supported"}, {"unavailable": True}):
            for summary in (summary_for(), summary_for(blockers=["production_exists_matches"])):
                page = self.page(summary, **state)
                self.assertEqual(controls(page, allow=("/attended/review-approve", "Approve this summary")), [], state)
                self.assertNotRegex(page, r"(?i)<(button|a)[^>]*>[^<]*(create in production|start onboarding|confirm)")

    def test_an_unlocked_flag_alone_adds_no_control(self):
        with unlocked_build:
            page = self.page(unlock=None)
        self.assertEqual(controls(page, allow=("/attended/review-approve", "Approve this summary")), [])
        self.assertNotIn("Production create is locked", page)
        self.assertIn(escape(dashboard.create_ui.gate_text("approval_missing")), page)   # the real gate's first refusal
        self.assertIn("Create blocked", page)

    def test_summary_and_create_approvals_are_labelled_differently(self):
        self.env.write_approvals(approval())
        page = self.page()
        card = page[page.index("<h2 class='pill'>Approval"):page.index("<h2 class='pill'>D2 record")]
        self.assertIn("Summary approval (4 hours)", card)
        self.assertIn("Create approval (7 days, one use)", card)
        self.assertIn("Not approved yet", card)  # the summary approval on the desktop
        for label in ("PROD-CE-0780-1", "owner@example.test", "2026-10-23", "chip-ok'>Valid</span>"):
            self.assertIn(label, card)
        self.assertNotIn("SECRET-REVISION-9", page)

    def test_create_approval_states(self):
        cases = {"expired": approval(issued="2026-10-01T09:00:00Z", expires="2026-10-08T09:00:00Z"),
                 "used": approval(consumed_at="2026-10-16T10:00:00Z")}
        for label, data in cases.items():
            self.env.write_approvals(data)
            card = self.page()
            self.assertIn("Expired" if label == "expired" else "Used", card[card.index("Create approval (7 days"):])
        self.env.approvals.write_text("garbage", encoding="utf-8")
        unreadable = self.page()
        self.assertIn("cannot be read", unreadable[unreadable.index("Create approval (7 days"):])
        self.env.approvals.unlink()
        none = self.page()
        self.assertIn("No create approval recorded", none)

    def test_on_the_server_unpublished_state_says_unavailable_never_not_approved(self):
        page = self.page(unavailable=True)
        card = page[page.index("<h2 class='pill'>Approval"):page.index("<h2 class='pill'>D2 record")]
        self.assertNotIn("Not approved yet", card)
        self.assertNotIn("No create approval recorded", card)
        self.assertGreaterEqual(card.count("unavailable on this server"), 2)
        self.assertIn("unavailable on this server", page[page.index("<h2 class='pill'>D2 record"):])

    def test_summary_approval_wording_is_kept(self):
        page = self.page(summary_for(blockers=["production_exists_matches"]))
        for text_ in ("Approve this summary", "disabled aria-disabled='true'", "already matches this CO", "creates nothing",
                      "Duplicate validation: production only"):
            self.assertIn(text_, page)

    def test_readiness_steps_follow_the_record_and_never_become_controls(self):
        def dry_run_item(page):
            start = page.index("<span class='what'>Dry run done<")
            return page[page.rindex("<li>", 0, start):page.index("</li>", start)]
        self.assertIn("chip-warn'>Locked</span>", dry_run_item(self.page()))
        self.env.write_record(record("CO-0780", "dry_run_passed"))
        one = self.page()
        self.assertIn("chip-ok'>Ready</span>", dry_run_item(one))
        self.assertIn("Dry run passed", one)   # the D2 record
        self.assertNotIn("<h2 class='pill'>Create</h2>", one)   # the old second Create card is gone
        self.assertEqual(controls(one, allow=("/attended/review-approve", "Approve this summary")), [])

    def test_d2_record_shows_only_allowlisted_fields(self):
        self.env.write_record(record("CO-0780", "readback_verified", tenant_name="<b>State</b>", plan_summary="CE, 1 year"))
        page = self.page()
        start = page.index("<h2 class='pill'>D2 record")
        card = page[start:page.index("</section>", start)]
        for shown in ("Verified", "&lt;b&gt;State&lt;/b&gt;", "a" * 24, "b" * 32, "created.example", "CE, 1 year",
                      "case_2_new_ce_only", "PROD-CE-0785-1", "Editable parameters"):
            self.assertIn(shown, card, shown)
        self.assertNotIn("<b>State</b>", page)
        for hidden in ("SECRET-REVISION-9", "response_shape", "salesforce_revision", "form_fields_checked"):
            self.assertNotIn(hidden, page)
        self.assertIn("Wrong create: the owner edits the parameters in BackOffice; the delete path is unknown.", card)

    def test_d2_record_secret_like_text_is_redacted_before_it_is_shown(self):
        self.env.write_record(record("CO-0780", "dry_run_passed", plan_summary="password=hunter2 Bearer abcdefghijklmnop"))
        page = self.page()
        for secret in ("hunter2", "Bearer abcdefghijklmnop"):
            self.assertNotIn(secret, page)

    def test_d2_states_none_unreadable(self):
        none = self.page()
        self.assertIn("No create record", none[none.index("<h2 class='pill'>D2 record"):])
        folder = self.env.parent / pcr.RECORDS_SUBDIR
        folder.mkdir()
        (folder / "CO-0001-20261016T120000Z.json").write_text(json.dumps({"co_reference": "CO-0001", "outcome": "refused",
                                                                         "recorded_at": "2026-10-16T12:00:00Z", "redactions": 0,
                                                                         "hand_edited": "RAW-SECRET-JSON"}), encoding="utf-8")
        bad = self.page()
        card = bad[bad.index("<h2 class='pill'>D2 record"):]
        self.assertIn("Create records unreadable", card)
        self.assertNotIn("RAW-SECRET-JSON", bad)
        self.assertNotIn("No create record", card[:card.index("</section>")])

    def test_the_create_card_owns_the_uuid_step_and_a_locked_build_shows_no_control(self):
        self.assertNotIn("UUID writeback to Salesforce", self.page())   # no separate, contradicting UUID card
        self.env.write_record(record("CO-0780", "readback_verified"))
        verified = self.page()
        self.assertNotIn("UUID writeback to Salesforce", verified)
        create = verified[verified.index("<h2 id='co-create-title'"):]
        self.assertIn("Created and verified", create[:create.index("</section>")])
        self.assertEqual(controls(verified, allow=("/attended/review-approve", "Approve this summary")), [])

    def test_values_are_escaped(self):
        hostile = "<img src=x onerror=alert(1)>"
        summary = summary_for(account=hostile, values={"tenant_names": [hostile], "domains": [hostile],
                                                       "plan": {hostile: hostile}},
                              production_check={"status": "exists_other", "reason": hostile, "tenants_checked": 1,
                                                "candidates": [{"name": hostile, "domain": hostile, "live_paid": True,
                                                                "deleted": False, "enabled": True, "license_type": hostile}]},
                              blockers=["production_exists_other", hostile])
        page = self.page(summary, row={**ROW, "Account_UUID__c": hostile})
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)

    def test_the_handler_serves_the_page_and_reads_the_row_for_the_uuid_check(self):
        with patched(self.env), patch.object(dashboard, "build_review_summary", return_value=(summary_for(), EXTRAS)), \
                patch.object(dashboard, "detail_row", return_value=dict(ROW)):
            request = areas_module.get("/onboard/CO-0780")
        status, page = request.pages[0]
        self.assertEqual(status, 200)
        self.assertIn("chip-ok'>Empty</span>", page)
        with patched(self.env), patch.object(dashboard, "build_review_summary", return_value=(summary_for(), EXTRAS)), \
                patch.object(dashboard, "detail_row", side_effect=dashboard.ReadUnavailable()):
            page = areas_module.get("/onboard/CO-0780").pages[0][1]
        self.assertNotIn("chip-ok'>Empty</span>", page)


class ApprovalsPageTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.tmp.cleanup)

    def page(self, **kwargs):
        status, html = page_of("/prod/approvals", self.env, **kwargs)
        self.assertEqual(status, 200)
        return html

    def test_lists_create_approvals_read_only_and_labels_both_kinds(self):
        self.env.write_approvals(
            approval(), approval("CO-0790", "PROD-CE-0790-1", issued="2026-10-01T09:00:00Z", expires="2026-10-08T09:00:00Z"),
            approval("CO-0791", "PROD-CE-0791-1", consumed_at="2026-10-16T10:00:00Z"))
        self.env.summary_approvals.write_text(json.dumps({"CO-0780": {
            "operator": "desktop-operator", "approved_at": "2026-10-16T10:00:00Z", "expires_at": "2026-10-16T14:00:00Z",
            "route": "case_2_new_ce_only", "source_revision": "SECRET-REVISION-9", "summary_hash": "a" * 64}}), encoding="utf-8")
        page = self.page()
        self.assertIn("<h1>Approvals</h1>", page)
        self.assertIn("envpill prod", page)
        self.assertIn("Create approvals (7 days, one use)", page)
        self.assertIn("Summary approvals (4 hours)", page)
        create = page[page.index("Create approvals (7 days"):page.index("Summary approvals (4 hours)")]
        self.assertEqual(headers(create[create.index("<table"):create.index("</table>")]),
                         ["Approval", "CO", "Route", "Issued by", "Issued", "Expires", "Status"])
        for shown in ("PROD-CE-0780-1", "PROD-CE-0790-1", "PROD-CE-0791-1", "owner@example.test", "2026-10-23",
                      "chip-ok'>Valid</span>", "chip-warn'>Expired</span>", "chip-neutral'>Used</span>"):
            self.assertIn(shown, create)
        summary = page[page.index("Summary approvals (4 hours)"):]
        self.assertIn("desktop-operator", summary)
        self.assertIn("chip-ok'>Valid</span>", summary)
        for hidden in ("SECRET-REVISION-9", "a" * 64, "summary_hash", "source_revision"):
            self.assertNotIn(hidden, page)
        self.assertEqual(controls(page), [])

    def test_no_file_is_no_approvals_recorded_on_the_desktop_and_unavailable_on_the_server(self):
        page = self.page()
        self.assertEqual(page.count("No approvals recorded"), 2)
        self.assertNotIn("unavailable on this server", page)
        server = self.page(unavailable=True)
        self.assertNotIn("No approvals recorded", server)
        self.assertEqual(server.count("unavailable on this server"), 2)
        self.assertNotIn("Not approved yet", server)

    def test_corrupt_files_say_so_and_approve_nothing(self):
        self.env.approvals.write_text("garbage", encoding="utf-8")
        self.env.summary_approvals.write_text("garbage", encoding="utf-8")
        page = self.page()
        self.assertEqual(page.count("cannot be read"), 2)
        self.assertNotIn("chip-ok'>Valid", page)
        self.assertNotIn("No approvals recorded", page)

    @unlocked_build
    def test_the_clone_strip_and_lock_chip(self):
        page = self.page()
        self.assertIn("Clone 2 h 10 min old, limit 6 h &middot; trusted", page)
        self.assertNotIn("Create locked", page)   # unlocked build: no stale lock chip
        with locked_create_build:
            self.assertIn("<span class='chip chip-warn'>Create locked</span>", self.page())

    def test_a_bad_record_cannot_inject_markup(self):
        self.env.write_approvals(approval(issued_by="<script>alert(1)</script>"))
        page = self.page()
        self.assertNotIn("<script>alert", page)
        self.assertIn("cannot be read", page)

    def test_the_vm_style_state_files_alone_decide(self):
        self.env.write_approvals(approval())
        server = self.page(unavailable=True)
        self.assertIn("PROD-CE-0780-1", server)  # a published file is shown read-only on a server too


class LegacyUrlsAndSafetyTests(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.tmp.cleanup)

    def test_unknown_routes_404_before_any_salesforce_read(self):
        with patched(self.env), patch.object(dashboard, "queue_rows", side_effect=AssertionError("read")), \
                patch.object(dashboard, "detail_row", side_effect=AssertionError("read")), \
                patch.object(dashboard, "queue_source_rows", side_effect=AssertionError("read")):
            for path in ("/nope", "/prod/nope", "/prod/approvals/x", "/onboard/nope", "/onboard/CO-12", "/dev/nope", "/dev/x/y",
                         "/prod/create", "/onboard/CO-0780/create", "/production/create"):
                request = areas_module.get(path)
                self.assertEqual(request.pages[0][0], 404, path)
                self.assertEqual(request.redirects, [], path)

    def test_old_urls_keep_working(self):
        with areas_module.areas():
            for path in ("/prod", "/prod/matches", "/prod/tenants", "/dev/onboarded", "/inventory", "/history", "/dev/history"):
                with patch.object(dashboard, "render_history", return_value="<h1>History</h1>"):
                    request = areas_module.get(path)
                self.assertTrue(request.pages or request.redirects, path)
                if request.pages:
                    self.assertEqual(request.pages[0][0], 200, path)
        self.assertEqual(areas_module.get("/tenants").redirects, ["/prod/tenants"])

    def test_production_pages_have_no_post_route_and_the_runner_stays_locked(self):
        self.assertIs(readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        # The guarded create/UUID routes (docs/53) are the ONLY allowed exception: they exist as POST routes but refuse
        # every request while a lock constant is False (tests/test_production_create_ui.py proves it).
        from integration.onboarding import production_create_ui as create_ui
        for route in dashboard.POST_ROUTES - create_ui.POST_PATHS:
            self.assertNotRegex(route, r"production-create|create-production|uuid-writeback")
        self.assertFalse([route for route in dashboard.POST_ROUTES if route.startswith(("/prod", "/dev", "/onboard"))])
        for path in ("/", "/onboard", "/prod/approvals", "/onboard/CO-0780"):
            self.assertNotIn(path, dashboard.POST_ROUTES)
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())

    def test_the_dashboard_never_runs_a_production_create_or_issues_an_approval(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        for forbidden in ("import production_create\n", "import production_create ", "production_create.run", "production_create import",
                          "production_approval.issue", "production_approval.consume", "salesforce_uuid_writeback.update_argv",
                          "production_create_record.record(", "run_target"):
            self.assertNotIn(forbidden, source)
        body = source[source.index("# ---- Production-first pages"):source.index("# Dashboard login (2026-10-03")]
        for forbidden in ("sf_write_json", "post_form", "write_text", "subprocess", "playwright", "webbrowser", "launch",
                          "record_runner", "bootstrap_leonardo_session", ".issue(", ".consume("):
            self.assertNotIn(forbidden, body)

    @unlocked_build
    def test_the_lock_is_the_gate_s_build_lock_never_the_process_environment(self):
        # Owner feedback 2026-10-18: the per-CO session unlock comes from the approval record inside the gate.
        with patch.object(readiness, "production_unlock_problem", side_effect=AssertionError("never asked")), \
                patch.object(dashboard, "vm_mode", return_value=False):
            self.assertEqual(dashboard._production_lock(), (False, None))
            with patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", False):
                self.assertEqual(dashboard._production_lock(), (True, "production_runner_not_supported"))
            with patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", False):
                self.assertEqual(dashboard._production_lock(), (True, "production_dry_run_not_supported"))
        with patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard._production_lock(), (True, "vm_read_only"))
        with patch.object(dashboard.create_ui, "build_lock", side_effect=RuntimeError("boom")):
            self.assertEqual(dashboard._production_lock(), (True, "production_lock_unreadable"))  # unreadable is locked

    def test_history_and_tenants_pages_sit_in_production(self):
        with areas_module.areas():
            self.assertIn("envpill prod", dashboard.page_history(None, failed=True))
            self.assertIn("envpill prod", dashboard.render_inventory(env="prod-clone"))
            self.assertIn("envpill dev", dashboard.render_inventory(env="dev"))

    def test_legacy_readiness_pages_still_render_read_only(self):
        with areas_module.areas():
            for page in (dashboard.render_prod_readiness("", True), dashboard.render_prod_matches(True)):
                self.assertEqual(areas_module.forms(page), [])
                self.assertIn("envpill prod", page)
                self.assertIn("class='active'>Onboarding</a>", page)

    def test_the_co_production_view_carries_the_lock_footer(self):
        row = areas_module.layout.row_for(areas_module.layout.SURFACE)
        with areas_module.areas(), patch.object(dashboard, "production_now", return_value=NOW):
            page = dashboard.page_detail_production("CO-0702", row, areas_module.MATCHES["CO-0702"])
        self.assertIn("Production create is locked in the dashboard: this page is guidance only.", page)
        self.assertEqual(controls(page), [])


if __name__ == "__main__":
    unittest.main()
