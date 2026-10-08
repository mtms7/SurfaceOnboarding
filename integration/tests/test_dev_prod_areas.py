"""Dashboard split into Leonardo Development and Production (owner decision 2026-10-07).

Every reader is stubbed: no Salesforce, no Leonardo, no Redash, no network. Production pages are GET-only.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from integration.onboarding.dev_onboarded import dev_onboarded_evidence, dev_status
from integration.tests import test_co_page_layout as layout
from integration.tests.test_production_match import CO, GOOD, PLAN, snapshot, tenant
from integration.onboarding import production_match as pm
import tools.serve_attended_open_onboardings_dashboard as dashboard

ENGINES = dashboard.RENEWAL_ENGINES
CASE3 = dashboard.CASE3_ENGINE


def readback(letter, state="Account Scanning"):
    return {"surface_account_id": letter * 20, "account_uuid": letter.lower() * 32, "leonardo_state": state,
            "observed_on": "2026-10-06", "source": "Leonardo Development Details readback"}


def run_record(**extra):
    return {"source_revision": "rev1", "result": "readback_verified", "completed_on": "2026-10-06T10:00:00",
            "license_start_entered": "2026-10-06", "license_end_entered": "2027-10-05", **extra}


def outcome(code="renewal_edit_verified", mode="confirm_write", write="verified", tenant_id=None, new="2029-10-26"):
    return {"result": code, "mode": mode, "leonardo_write": write, "observed_at": datetime(2026, 10, 7, 9, 30),
            "changes": 3, "added_domains": 0, "old_expiration": "2026-10-25", "new_expiration": new, "tenant_id": tenant_id}


def canned(status, **extra):
    base = {"status": status, "reason": "", "fields": [], "differs": [], "candidates": [], "target": None,
            "captured_at": "2026-10-05T11:30:00Z", "age_seconds": 1800, "tenants_checked": 5, "route": None}
    return {**base, **extra}


# Dev data: A create (CE), B create (Surface), C Case 3 create, D renewal verified edit on a mirror, E mirror only,
# F renewal dry run, G renewal edit attempted but unverified, H create uncertain, I renewal already current.
READBACKS = {f"CO-070{i}": readback(letter) for i, letter in enumerate("ABCDEFGHI", start=1)}
RECORDS = {
    "CO-0701": run_record(),
    "CO-0702": run_record(route=dashboard.SURFACE_ENGINE),
    "CO-0703": run_record(route=CASE3),
    "CO-0704": {"source_revision": "rev1", "route": "case_6_renew_both", "result": "renewal_edit_verified",
                "completed_on": "2026-10-07T09:30:00"},
    "CO-0708": {"source_revision": "rev1", "result": "runner_crashed", "completed_on": "2026-10-06T10:00:00",
                "license_start_entered": "2026-10-06", "license_end_entered": "2027-10-05"},
}
OUTCOMES = {
    "CO-0704": outcome(tenant_id="D" * 20),
    "CO-0706": outcome("renewal_dry_run_planned", "dry_run", "not_performed"),
    "CO-0707": outcome("renewal_saved_unverified", "confirm_write", "attempted_unverified"),
    "CO-0709": outcome("renewal_already_current", "dry_run", "not_performed", tenant_id="I" * 20),
}
MIRRORS = frozenset({"D" * 20, "E" * 20, "F" * 20})
ROWS = [
    {"Name": "CO-0701", "Account__r.Name": "Acme CE", "Onboarding_Product__c": "Credential Exposure",
     "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "Account Scanning"},
    {"Name": "CO-0702", "Account__r.Name": "Beta Surface", "Onboarding_Product__c": "Surface",
     "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New"},
    {"Name": "CO-0710", "Account__r.Name": "Open Not Onboarded", "Onboarding_Product__c": "Surface",
     "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New"},
    {"Name": "CO-0704", "Account__r.Name": "Renewal Corp", "Onboarding_Product__c": "Surface & Credential Exposure",
     "Onboarding_Type__c": "Renewal of Existing Product", "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New"},
]
MATCHES = {
    "CO-0701": canned("exists_matches"),
    "CO-0702": canned("exists_differs", differs=["license_end"], fields=[{
        "code": "license_end", "label": "Licence end", "expected": "2027-10-18", "production": "2027-10-20",
        "result": "differs", "required": True}]),
    "CO-0703": canned("not_in_production"),
    "CO-0704": canned("exists_other"),
    "CO-0709": canned("not_in_production"),
    "CO-0710": canned("ambiguous"),
}


@contextlib.contextmanager
def areas(rows=ROWS, matches=MATCHES, state=RECORDS, readbacks=READBACKS, outcomes=OUTCOMES, mirrors=MIRRORS, checks=None,
          root=None):
    root = root or Path(tempfile.mkdtemp())
    dashboard.clear_display_cache()
    with contextlib.ExitStack() as stack:
        enter = stack.enter_context
        enter(patch.object(dashboard, "load_runner_state", return_value=state))
        enter(patch.object(dashboard, "attended_leonardo_readbacks", return_value=readbacks))
        enter(patch.object(dashboard, "attended_renewal_outcomes", return_value=outcomes))
        enter(patch.object(dashboard, "dev_mirror_ids", return_value=mirrors))
        enter(patch.object(dashboard, "queue_source_rows", return_value=[dict(row) for row in rows]))
        enter(patch.object(dashboard, "load_check_state", return_value=checks or {}))
        enter(patch.object(dashboard, "inventory_root", return_value=root))
        enter(patch.object(dashboard, "prefetch_display_read"))
        enter(patch.object(dashboard, "production_match_for",
                           side_effect=lambda ref, product=None, kind=None: matches.get(ref, canned("not_in_production"))))
        enter(patch.object(dashboard, "sf_json", side_effect=AssertionError("no Salesforce in area tests")))
        yield
    dashboard.clear_display_cache()


class FakeRequest:
    def __init__(self, path):
        self.path, self.redirects, self.pages, self.headers = path, [], [], {}

    def send_redirect(self, location):
        self.redirects.append(location)

    def send_page(self, status, page):
        self.pages.append((int(status), page))


def get(path):
    request = FakeRequest(path)
    with patch.object(dashboard, "login_required", return_value=False), \
            patch.object(dashboard, "request_origin_problem", return_value=None):
        dashboard.Handler.do_GET(request)
    return request


def without_search(page):
    """The page without the shell's sidebar search form, so button/post assertions cover the page content only."""
    return re.sub(r"<form class='sidesearch'.*?</form>", "", page, flags=re.S)


def forms(page):
    # The sidebar search form (GET /search, owner request 2026-10-07) is in every shell; any other form still fails.
    return [f for f in re.findall(r"<form[^>]*>", page, flags=re.I) if "action='/search'" not in f]


class NavigationTests(unittest.TestCase):
    ORDER = [("/", "Onboardings"), ("/dev/onboarded", "Onboarded on Dev"), ("/history", "History"), ("/inventory", "Tenants"),
             ("/prod", "Readiness"), ("/prod/matches", "Matches"), ("/prod/tenants", "Tenants"), ("/connection", "Sessions")]

    def test_groups_labels_and_order(self):
        shell = dashboard._app_shell("t", "", active="onboardings")
        aside = shell[shell.index("<aside"):shell.index("</aside>")]
        links = re.findall(r"<a href='([^']+)'(?: class='active')?>([^<]+)</a>", aside)
        self.assertEqual(links, self.ORDER)
        heads = re.findall(r"<div class='navhead'>(.*?)</div>", aside)
        self.assertEqual([re.sub(r"<[^>]+>", "", head) for head in heads],
                         ["LEONARDO · DEV", "PRODUCTION · BOread-only", "TOOLS"])
        self.assertIn("<span class='ro'>read-only</span>", aside)
        self.assertLess(aside.index("navgroup dev"), aside.index("navgroup prod"))
        self.assertLess(aside.index("navgroup prod"), aside.index("navgroup tools"))
        for icon in ("<svg", "<img", "&#x1f", "emoji"):
            self.assertNotIn(icon, aside)
        self.assertIn(".navgroup.dev a::before{background", dashboard.PENTERA_CSS)  # solid dot
        self.assertIn(".navgroup.prod a::before{background:transparent", dashboard.PENTERA_CSS)  # hollow dot

    def test_each_page_has_exactly_one_active_link_and_the_right_environment(self):
        expected = {"onboardings": "dev", "dev_onboarded": "dev", "history": "dev", "inventory": "dev", "prod": "prod",
                    "prod_matches": "prod", "prod_tenants": "prod", "connection": "tools"}
        pills = {"dev": "DEV · Leonardo", "prod": "PRODUCTION · read-only", "tools": "TOOLS"}
        for active, env in expected.items():
            shell = dashboard._app_shell("t", "<p>body</p>", active=active)
            self.assertEqual(shell.count("class='active'"), 1, active)
            self.assertIn("<div class='envbar " + env + "' aria-hidden='true'></div>", shell)
            self.assertIn("<span class='envpill " + env + "'>" + pills[env] + "</span>", shell)
            self.assertEqual(shell.count("class='envpill"), 1)

    def test_production_uses_the_warn_tokens_and_red_is_never_an_environment_colour(self):
        css = dashboard.PENTERA_CSS
        self.assertIn("--env-prod:var(--warn);--env-prod-bg:var(--warn-bg)", css)
        for rule in re.findall(r"\.(?:envbar|envpill|navgroup)[^{]*\{[^}]*\}", css):
            self.assertNotIn("--bad", rule)
            self.assertNotIn("#d92d20", rule)

    def test_the_pill_stays_visible_on_mobile(self):
        self.assertIn(".envrow{position:sticky;top:3px", dashboard.PENTERA_CSS)
        self.assertNotIn(".envpill{display:none", dashboard.PENTERA_CSS)
        mobile = dashboard.PENTERA_CSS[dashboard.PENTERA_CSS.index("@media(max-width:760px){.navgroup"):]
        self.assertNotIn("envpill", mobile.split("}}")[0])

    def test_real_pages_carry_their_environment(self):
        with areas():
            self.assertIn("envpill dev", dashboard.render_dev_onboarded())
            self.assertIn("envpill prod", dashboard.render_prod_readiness())
            self.assertIn("envpill prod", dashboard.render_prod_matches())
            self.assertIn("envpill dev", dashboard.render_inventory(env="dev"))
            self.assertIn("envpill prod", dashboard.render_inventory(env="prod-clone"))
            self.assertIn("envpill dev", dashboard.page_history(None, failed=True))
            self.assertIn("envpill dev", dashboard.page_queue(ROWS))

    def test_co_page_dev_and_production_views_have_their_pill_bar_and_segmented_control(self):
        row = layout.row_for(layout.SURFACE)
        with areas():
            dev = layout.render(row)
            prod = dashboard.page_detail_production("CO-0702", row, MATCHES["CO-0702"])
        self.assertIn("envpill dev", dev)
        self.assertIn("envbar dev", dev)
        self.assertIn("envpill prod", prod)
        self.assertIn("envbar prod", prod)
        for page, on in ((dev, "dev"), (prod, "prod")):
            seg = page[page.index("<div class='seg'"):page.index("</div>", page.index("<div class='seg'"))]
            self.assertIn("href='/co/CO-0702?env=dev'", seg)
            self.assertIn("href='/co/CO-0702?env=prod'", seg)
            self.assertEqual(seg.count("aria-current='page'"), 1)
            self.assertIn("href='/co/CO-0702?env=" + on + "' class='on'", seg)
            self.assertNotIn("<script", page)
            self.assertNotIn("<button", seg)


class RedirectAndRouteTests(unittest.TestCase):
    def test_old_tenants_url_is_a_303_to_production_tenants_keeping_filters(self):
        request = get("/tenants?q=acme&sort=scan&dir=desc&scan=COMPLETED&junk=1")
        self.assertEqual((request.redirects, request.pages), (["/prod/tenants?q=acme&sort=scan&dir=desc&scan=COMPLETED"], []))
        self.assertEqual(get("/tenants").redirects, ["/prod/tenants"])
        self.assertEqual(get("/inventory?env=prod-clone").redirects, ["/prod/tenants"])
        self.assertEqual(get("/inventory?env=prod-clone&q=x").redirects, ["/prod/tenants?q=x"])

    def test_redirect_helper_sends_see_other(self):
        sent = []

        class Raw(dashboard.Handler):
            def __init__(self):
                self.headers = {}

            def send_response(self, status, *_):
                sent.append(int(status))

            def send_header(self, *_):
                pass

            def end_headers(self):
                pass
        Raw().send_redirect("/prod/tenants")
        self.assertEqual(sent, [303])

    def test_dev_pages_and_history_alias_render(self):
        with areas():
            self.assertEqual(get("/dev/onboarded").pages[0][0], 200)
            self.assertIn("<h1>Onboarded on Dev</h1>", get("/dev/onboarded").pages[0][1])
            self.assertEqual(get("/inventory").pages[0][0], 200)
            self.assertIn("Tenants — Leonardo Development", get("/inventory").pages[0][1])
            with patch.object(dashboard, "render_history", return_value="<h1>History</h1>"):
                self.assertEqual(get("/history").pages[0][1], get("/dev/history").pages[0][1])

    def test_production_pages_render_and_the_clone_tenants_page_moved(self):
        with areas():
            for path in ("/prod", "/prod/matches", "/prod/tenants", "/prod?filter=differs&all=1"):
                request = get(path)
                self.assertEqual((request.redirects, request.pages[0][0]), ([], 200), path)
            self.assertIn("Tenants — Production (Redash clone)", get("/prod/tenants").pages[0][1])

    def test_production_routes_accept_get_only(self):
        for path in ("/prod", "/prod/matches", "/prod/tenants", "/dev/onboarded", "/tenants"):
            self.assertNotIn(path, dashboard.POST_ROUTES)
        self.assertFalse([route for route in dashboard.POST_ROUTES if route.startswith(("/prod", "/dev"))])

    def test_co_page_env_parameter(self):
        row = layout.row_for(layout.SURFACE)
        with areas(), patch.object(dashboard, "detail_row", return_value=row), \
                patch.object(dashboard, "page_detail", return_value="<p>dev view</p>") as dev, \
                patch.object(dashboard, "page_detail_production", return_value="<p>prod view</p>") as prod, \
                patch.object(dashboard, "prefetch_display_read"), patch.object(dashboard, "dealhub_needed", return_value=False), \
                patch.object(dashboard, "surface_commercial_readiness", return_value=None), \
                patch.object(dashboard, "peek_display_cache", return_value=None):
            self.assertEqual(get("/co/CO-0702?env=prod").pages[0][1], "<p>prod view</p>")
            self.assertEqual(prod.call_args.args[0], "CO-0702")
            self.assertEqual(prod.call_args.args[2]["status"], "exists_differs")
            for query in ("", "?env=dev", "?env=bogus"):
                self.assertEqual(get("/co/CO-0702" + query).pages[0][1], "<p>dev view</p>", query)
            self.assertEqual(dev.call_count, 3)


class DevOnboardedEvidenceTests(unittest.TestCase):
    def evidence(self, ref, **overrides):
        return dev_onboarded_evidence(ref, record=overrides.get("record", RECORDS.get(ref)),
                                      readback=overrides.get("readback", READBACKS.get(ref)),
                                      outcome=overrides.get("outcome", OUTCOMES.get(ref)),
                                      mirror_ids=overrides.get("mirrors", MIRRORS), renewal_engines=ENGINES,
                                      uncertain=overrides.get("uncertain", False))

    def test_a_verified_create_with_both_ids_counts(self):
        items = self.evidence("CO-0701")
        self.assertEqual([(i["code"], i["counts"], i["on"]) for i in items], [("create_readback_verified", True, "2026-10-06")])
        self.assertEqual(dev_status(items), "onboarded")

    def test_a_create_without_both_ids_does_not_count(self):
        for broken in (None, {"surface_account_id": "A" * 20}, {"account_uuid": "a" * 32}, {"surface_account_id": "", "account_uuid": "a" * 32}):
            self.assertEqual(self.evidence("CO-0701", readback=broken), [])

    def test_failed_or_uncertain_creates_never_count(self):
        self.assertEqual(self.evidence("CO-0708"), [])  # run stopped with the licence dates entered: uncertain, not evidence
        self.assertEqual(self.evidence("CO-0701", uncertain=True), [])
        self.assertEqual(self.evidence("CO-0701", record={**RECORDS["CO-0701"], "result": "duplicate_found"}), [])

    def test_renewals_count_only_verified_edits_or_already_current(self):
        verified = self.evidence("CO-0704")
        self.assertEqual([(i["code"], i["counts"]) for i in verified], [("renewal_edit_verified", True), ("dev_mirror", False)])
        self.assertEqual(dev_status(verified), "onboarded")
        current = self.evidence("CO-0709")
        self.assertEqual([(i["code"], i["counts"]) for i in current], [("renewal_already_current", True)])

    def test_dry_runs_attempted_unverified_and_mismatched_tenants_never_count(self):
        self.assertEqual([(i["code"], i["counts"]) for i in self.evidence("CO-0706")], [("dev_mirror", False)])
        self.assertEqual([(i["code"], i["counts"]) for i in self.evidence("CO-0707")], [])
        self.assertEqual(self.evidence("CO-0704", outcome=outcome(mode="dry_run"))[0]["code"], "dev_mirror")
        self.assertEqual(self.evidence("CO-0704", outcome=outcome(write="attempted_unverified"))[0]["code"], "dev_mirror")
        wrong = outcome(tenant_id="Z" * 20)  # the outcome belongs to another Dev tenant
        self.assertEqual([i["code"] for i in self.evidence("CO-0704", outcome=wrong)], ["dev_mirror"])
        self.assertEqual([(i["code"], i["counts"]) for i in self.evidence("CO-0704", uncertain=True)], [("dev_mirror", False)])

    def test_a_verified_mirror_alone_is_dev_mirror_not_onboarded(self):
        items = self.evidence("CO-0705")
        self.assertEqual([(i["code"], i["label"], i["counts"]) for i in items], [("dev_mirror", "Dev mirror", False)])
        self.assertEqual(dev_status(items), "mirror")
        self.assertEqual(dev_status([]), "none")

    def test_dashboard_helpers_use_the_local_evidence_only(self):
        with areas():
            evidence = dashboard.dev_evidence_map()
            self.assertEqual(sorted(dashboard.dev_onboarded_refs()), ["CO-0701", "CO-0702", "CO-0703", "CO-0704", "CO-0709"])
            self.assertEqual(sorted(dashboard.dev_onboarded_refs(evidence)), sorted(dashboard.dev_onboarded_refs()))
            self.assertNotIn("CO-0705", dashboard.dev_onboarded_refs())
            self.assertNotIn("CO-0706", dashboard.dev_onboarded_refs())
            self.assertNotIn("CO-0707", dashboard.dev_onboarded_refs())
            self.assertNotIn("CO-0708", dashboard.dev_onboarded_refs())


class OnboardedOnDevPageTests(unittest.TestCase):
    def page(self, **kwargs):
        with areas(**kwargs):
            return dashboard.render_dev_onboarded()

    def test_table_columns_and_rows(self):
        page = self.page()
        for column in ("CO", "Account", "Route", "Onboarded", "Account ID / UUID", "Licence start &ndash; end", "Evidence", "Production"):
            self.assertIn(">" + column + "</th>", page)
        self.assertEqual(page.count("<tr><td class='stick'>"), 5)
        for ref in ("CO-0701", "CO-0702", "CO-0703", "CO-0704", "CO-0709"):
            self.assertIn(">" + ref + "</a>", page)
        for ref in ("CO-0705", "CO-0706", "CO-0707", "CO-0708", "CO-0710"):
            self.assertNotIn(">" + ref + "</a>", page)
        self.assertIn("2 Dev mirror(s) not counted", page)  # CO-0705 (mirror only) and CO-0706 (dry run on a mirror)
        self.assertIn("Route unknown", page)  # CO-0709 is not in the open queue and has no run record

    def test_ids_follow_the_product_rule_with_full_values(self):
        page = self.page()
        rows = {ref: page[page.index(">" + ref + "</a>"):page.index("</tr>", page.index(">" + ref + "</a>"))]
                for ref in ("CO-0701", "CO-0702", "CO-0703", "CO-0704")}
        uuid_a, id_a = "a" * 32, "A" * 20
        self.assertIn("UUID <code style='user-select:all'>" + uuid_a + "</code>", rows["CO-0701"])  # CE -> UUID only
        self.assertNotIn(id_a, rows["CO-0701"])
        self.assertIn("ID <code style='user-select:all'>" + "B" * 20 + "</code>", rows["CO-0702"])  # Surface -> ID only
        self.assertNotIn("b" * 32, rows["CO-0702"])
        self.assertIn("B" * 20, rows["CO-0702"])
        for both in ("CO-0703", "CO-0704"):  # Case 3 and renewals -> both
            letter = "C" if both == "CO-0703" else "D"
            self.assertIn(letter * 20, rows[both])
            self.assertIn(letter.lower() * 32, rows[both])

    def test_licence_dates_are_the_entered_ones_and_renewals_show_the_new_expiration(self):
        page = self.page()
        self.assertIn("2026-10-06 → 2027-10-05", page)
        self.assertIn("— → 2029-10-26", page)
        self.assertIn("entered in Leonardo Development", page)

    def test_evidence_chips_and_production_chip_link_to_the_production_view(self):
        page = self.page()
        self.assertIn("<span class='chip chip-ok'>Readback verified</span>", page)
        self.assertIn("<span class='chip chip-ok'>Renewal edit verified</span>", page)
        self.assertIn("<a href='/co/CO-0701?env=prod'><span class='chip chip-ok'>Matches</span></a>", page)
        self.assertIn("<a href='/co/CO-0702?env=prod'><span class='chip chip-warn'>Differs</span></a>", page)
        self.assertIn("<a href='/co/CO-0703?env=prod'><span class='chip chip-neutral'>Not in production</span></a>", page)

    def test_it_is_a_get_page_without_forms_scripts_or_dev_mirrors_as_production_evidence(self):
        page = self.page()
        self.assertEqual(forms(page), [])
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("Dev mirror</span>", page)  # a mirror is never shown as a result row

    def test_empty_state_and_an_unreadable_queue_still_render(self):
        empty = self.page(readbacks={}, state={}, outcomes={})
        self.assertIn("No CO has verified Dev evidence yet.", empty)
        with areas(), patch.object(dashboard, "queue_source_rows", side_effect=dashboard.ReadUnavailable()):
            page = dashboard.render_dev_onboarded()
        self.assertIn("account names are not shown", page)
        self.assertIn(">CO-0701</a>", page)

    def test_values_are_escaped(self):
        rows = [{**ROWS[0], "Account__r.Name": "<img src=x onerror=alert(1)>"}]
        page = self.page(rows=rows)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)


class ProductionReadinessTests(unittest.TestCase):
    def page(self, flt="", all_open=False, **kwargs):
        with areas(**kwargs):
            return dashboard.render_prod_readiness(flt, all_open)

    def refs(self, page):
        return re.findall(r"<td class='stick'><a class='co' href='/co/(CO-\d+)\?env=prod'>", page)

    def test_default_scope_is_only_cos_onboarded_on_dev_needs_action_first(self):
        page = self.page()
        self.assertEqual(sorted(self.refs(page)), ["CO-0701", "CO-0702", "CO-0703", "CO-0704", "CO-0709"])
        # Resolve duplicate (CO-0704) first, then Review fields (CO-0702), then None (the rest, by CO).
        self.assertEqual(self.refs(page), ["CO-0704", "CO-0702", "CO-0701", "CO-0703", "CO-0709"])
        self.assertNotIn("CO-0710", page)  # open but not onboarded
        self.assertIn("Showing only COs onboarded on Dev.", page)
        self.assertIn("href='/prod?all=1'", page)

    def test_all_toggle_lists_every_open_co(self):
        page = self.page(all_open=True)
        self.assertEqual(sorted(self.refs(page)), ["CO-0701", "CO-0702", "CO-0703", "CO-0704", "CO-0709", "CO-0710"])
        self.assertIn("Showing every open CO.", page)
        self.assertIn("Show only onboarded on Dev", page)

    def test_columns_chips_actions_and_differing_fields(self):
        page = self.page()
        for column in ("CO", "Account", "Route", "Dev status", "Production match", "Differing fields", "Action needed"):
            self.assertIn(">" + column + "</th>", page)
        self.assertIn("<span class='chip chip-ok'>Onboarded</span>", page)
        self.assertIn("Licence end", page)  # the differing field
        self.assertIn(">Review fields</td>", page)
        self.assertIn(">Resolve duplicate</td>", page)
        self.assertIn(">None</td>", page)
        stale = self.page(all_open=True, matches={**MATCHES, "CO-0701": canned("clone_unavailable", reason="inventory_snapshot_stale")})
        self.assertIn("Refresh clone: <code>tools\\redash_inventory_collector.py --collect</code>", stale)

    def test_the_summary_strip_counts_act_as_filters(self):
        page = self.page(all_open=True)
        for label, number in (("All", 6), ("Matches", 1), ("Differs", 1), ("Not in production", 2), ("Blocked", 2),
                              ("Clone unavailable", 0)):
            self.assertRegex(page, r"<a class='fchip(?: on)?' href='[^']+'>" + label + r" <b>" + str(number) + r"</b>")
        self.assertIn("href='/prod?filter=blocked&amp;all=1'", page)
        self.assertEqual(sorted(self.refs(self.page("blocked", True))), ["CO-0704", "CO-0710"])
        self.assertEqual(self.refs(self.page("matches", True)), ["CO-0701"])
        self.assertEqual(self.refs(self.page("differs")), ["CO-0702"])
        self.assertEqual(self.refs(self.page("not_in_production")), ["CO-0703", "CO-0709"])
        self.assertEqual(self.refs(self.page("clone_unavailable", True)), [])
        self.assertEqual(len(self.refs(self.page("bogus"))), 5)  # an unknown filter shows everything in scope

    def test_clone_unavailable_count_shows_the_clone_age(self):
        from integration.tests.test_redash_inventory import FAKE_KEY, GET, FakeOpener, result, row
        import tools.redash_inventory_collector as collector
        root = Path(tempfile.mkdtemp())
        collector.collect(FakeOpener({GET: result([row(1)], "2026-10-05T11:30:00")}), FAKE_KEY, root=root,
                          now=datetime(2026, 10, 5, 12, 0, tzinfo=__import__("datetime").timezone.utc))
        page = self.page(all_open=True, root=root, matches={**MATCHES, "CO-0701": canned("clone_unavailable")})
        self.assertRegex(page, r"Clone unavailable <b>1</b> · clone age \d+ h")
        self.assertIn("older than 6 h: not trusted", page)
        missing = self.page()
        self.assertIn("Production clone unusable", missing)

    def test_no_forms_no_scripts_no_refresh_button(self):
        for page in (self.page(), self.page(all_open=True), self.page("differs")):
            self.assertEqual(forms(page), [])
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("<button", without_search(page))
            self.assertNotIn("method='post'", page.lower())
            self.assertNotIn("/attended/", page)

    def test_a_failed_source_read_shows_as_unavailable_never_as_clear(self):
        with areas(), patch.object(dashboard, "production_match_for", side_effect=RuntimeError("boom")):
            page = dashboard.render_prod_readiness("", True)
        self.assertEqual(page.count("<span class='chip chip-warn'>Clone unavailable</span>"), 6)
        self.assertIn("Check CO source data", page)
        self.assertNotIn("chip-ok'>Matches</span>", page)

    def test_matches_page_is_current_only_sorted_by_status_with_the_recorded_gate_checks(self):
        checks = {"CO-0711": {"kind": "production_duplicate", "result": "duplicate_production_clone_match",
                              "completed_on": "2026-10-06T09:15:00", "detail": "{}"},
                  "CO-0712": {"kind": "validation", "result": "validation_recorded", "completed_on": "2026-10-06T09:00:00"}}
        with areas(checks=checks):
            page = dashboard.render_prod_matches(True)
        statuses = re.findall(r"<tr><td><span class='chip [^']+'>([^<]+)</span></td><td class='stick'>", page)
        self.assertEqual(statuses, ["Other tenants only", "Ambiguous", "Differs", "Not in production", "Not in production",
                                    "Matches"])
        self.assertIn("Production clone unusable", page)  # no clone collected in this temp root
        self.assertIn("Latest Start-gate checks", page)
        self.assertIn("CO-0711", page)
        self.assertIn("2026-10-06 09:15:00", page)
        self.assertIn("Already in production", page)
        self.assertNotIn("CO-0712", page)  # only production_duplicate checks
        self.assertEqual(forms(page), [])
        self.assertIn("nothing is stored", page.lower())

    def test_matches_page_has_no_persistence(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        body = source[source.index("def render_prod_matches"):source.index("def _ids_cell")]
        for forbidden in ("write_text", "_write_json", "open(", "record_check", "json.dump"):
            self.assertNotIn(forbidden, body)


class ProductionCoViewTests(unittest.TestCase):
    ROW = layout.row_for(layout.SURFACE)

    def real_match(self):
        return pm.production_match(CO, {**PLAN, "license_end": PLAN["license_end"].replace(day=19)},
                                   snapshot(tenant(spycloud=True, **GOOD), tenant("Acme", "acme.example", kind="Trial", ident="trial")))

    def view(self, match=None, checks=None):
        with areas(checks=checks):
            return dashboard.page_detail_production("CO-0702", self.ROW, match or self.real_match())

    def test_it_has_no_form_and_no_button_and_no_script(self):
        for match in (self.real_match(), canned("not_in_production"), canned("clone_unavailable", reason="inventory_snapshot_missing")):
            page = self.view(match)
            self.assertEqual(forms(page), [])
            self.assertNotIn("<button", without_search(page))
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("method='post'", page.lower())

    def test_it_shows_the_status_the_comparison_and_the_matched_tenants(self):
        page = self.view()
        self.assertIn("<h2 class='pill'>Production match</h2><span class='chip chip-warn'>Differs</span>", page)
        for header in ("Field", "Salesforce / DealHub (expected)", "Production (clone)", "Result"):
            self.assertIn(">" + header + "</th>", page)
        self.assertIn("<td>Licence end</td><td>2027-10-19</td><td>2027-10-18</td><td><span class='chip chip-warn'>Differs</span>", page)
        self.assertIn("<td>SpyCloud<span class='sub'>informational</span></td><td>ON (default)</td><td>ON</td>"
                      "<td><span class='chip chip-neutral'>Info</span></td>", page)  # informational: never a difference
        self.assertNotIn("SpyCloud OFF", page)
        self.assertIn("informational", page)
        self.assertIn("Not checked", page)  # Salesforce account id
        self.assertIn("Matched production tenants", page)
        self.assertIn("<span class='chip chip-ok'>Live, paid</span>", page)
        self.assertIn("Redash saved query", page)
        self.assertIn("never a clearance", page)

    def test_it_has_the_before_migration_checklist_as_text(self):
        page = self.view()
        self.assertIn("Before migration", page)
        self.assertEqual(page.count("<li>"), len(dashboard.BEFORE_MIGRATION))
        self.assertIn("Onboarding Stage", page)
        self.assertIn("redash_inventory_collector.py --collect", page)

    def test_unavailable_and_blocked_states_explain_themselves(self):
        gone = self.view(canned("clone_unavailable", reason="inventory_snapshot_stale"))
        self.assertIn("inventory_snapshot_stale", gone)
        self.assertNotIn("<table class='dense'><thead><tr><th scope='col'>Field", gone)
        other = self.view(canned("exists_other", reason="Only trial ... blocked", candidates=[
            {"id": "t1", "name": "<b>Acme</b>", "domain": "acme.example", "created": 1759622400000, "deleted": True,
             "enabled": False, "live_paid": False, "license_type": "Trial"}]))
        self.assertIn("&lt;b&gt;Acme&lt;/b&gt;", other)
        self.assertIn("<span class='chip chip-bad'>Deleted</span>", other)
        self.assertIn("2025-10-05", other)

    def test_the_recorded_start_gate_marker_is_reused(self):
        checks = {"CO-0702": {"kind": "production_duplicate", "result": "duplicate_production_clone_match",
                              "detail": '{"matches": [{"name": "Acme", "id": "x", "created": 1759622400000}]}'}}
        self.assertIn("This CO already has a tenant in production.", self.view(checks=checks))

    def test_dev_chip_in_the_segment_uses_local_evidence_and_the_prod_chip_the_match(self):
        page = self.view()
        seg = page[page.index("<div class='seg'"):page.index("</div>", page.index("<div class='seg'"))]
        self.assertIn(">Dev <span class='chip chip-ok'>Onboarded</span></a>", seg)  # CO-0702 is onboarded on Dev
        self.assertIn(">Production <span class='chip chip-warn'>Differs</span></a>", seg)

    def test_dev_view_peeks_only_and_never_reads_production(self):
        row = layout.row_for(layout.SURFACE)
        with areas(), patch.object(dashboard, "production_match_for", side_effect=AssertionError("no production read")):
            page = layout.render(row)
        self.assertIn("<span class='chip chip-neutral'>Not checked</span>", page)
        with areas(), patch.object(dashboard, "peek_display_cache", return_value=canned("exists_matches")):
            self.assertIn(">Production <span class='chip chip-ok'>Matches</span></a>", layout.render(row))


class QueueSplitTests(unittest.TestCase):
    def render(self, rows=ROWS, **kwargs):
        with areas(**kwargs), patch.object(dashboard, "queue_rows", return_value=[dict(row) for row in rows]), \
                patch.object(dashboard, "queue_start_dates", return_value={}), \
                patch.object(dashboard, "closed_history", return_value=None), \
                patch.object(dashboard, "warm_co_pages"):
            return dashboard.render_dashboard()

    def test_cos_onboarded_on_dev_leave_the_queue_and_a_link_counts_them(self):
        page = self.render()
        self.assertIn("<a href='/dev/onboarded'>5 onboarded on Dev &rarr;</a>", page)
        self.assertIn(">CO-0710<", page)
        for ref in ("CO-0701", "CO-0702", "CO-0704"):
            self.assertNotIn(">" + ref + "<", page)

    def test_a_dev_mirror_or_unverified_run_stays_in_the_queue(self):
        rows = [{**ROWS[2], "Name": "CO-0705"}, {**ROWS[2], "Name": "CO-0708"}, {**ROWS[2], "Name": "CO-0706"}]
        page = self.render(rows)
        for ref in ("CO-0705", "CO-0708", "CO-0706"):
            self.assertIn(">" + ref + "<", page)
        self.assertIn(">5 onboarded on Dev &rarr;</a>", page)

    def test_an_unreadable_evidence_store_never_hides_a_co(self):
        with patch.object(dashboard, "dev_onboarded_refs", side_effect=OSError()):
            with areas(), patch.object(dashboard, "queue_rows", return_value=[dict(r) for r in ROWS]), \
                    patch.object(dashboard, "dev_onboarded_refs", side_effect=OSError()), \
                    patch.object(dashboard, "queue_start_dates", return_value={}), \
                    patch.object(dashboard, "closed_history", return_value=None), patch.object(dashboard, "warm_co_pages"):
                page = dashboard.render_dashboard()
        self.assertIn(">CO-0701<", page)
        self.assertIn(">0 onboarded on Dev &rarr;</a>", page)


class SafetyTests(unittest.TestCase):
    def test_no_production_page_has_a_post_form_script_or_write_route(self):
        with areas():
            pages = [dashboard.render_prod_readiness(), dashboard.render_prod_readiness("", True), dashboard.render_prod_matches(),
                     dashboard.render_inventory(env="prod-clone", top=dashboard.PRODUCTION_GATE_NOTE),
                     dashboard.page_detail_production("CO-0702", layout.row_for(layout.SURFACE), MATCHES["CO-0702"])]
        for page in pages:
            self.assertEqual(forms(page), [])
            self.assertNotIn("<script", page.lower())
            self.assertNotIn("/attended/", page)
            self.assertNotIn("inventory-refresh", page)

    def test_new_code_never_touches_a_production_session_or_writes(self):
        source = Path(dashboard.__file__).read_text(encoding="utf-8")
        body = source[source.index("# ---- Two areas:"):source.index("# Dashboard login (2026-10-03")]
        for forbidden in ("sf_write_json", "post_form", "bootstrap_leonardo_session", "check_leonardo_session",
                          "open_attended_production_backoffice_login", "record_runner", "write_text", "_write_json",
                          "launch", "subprocess", "playwright", "webbrowser"):
            self.assertNotIn(forbidden, body)

    def test_dev_and_production_rows_never_share_a_table(self):
        with areas():
            dev = dashboard.render_dev_onboarded()
            prod = dashboard.render_prod_readiness("", True)
        self.assertEqual(dev.count("<table"), 1)
        self.assertEqual(prod.count("<table"), 1)
        self.assertIn("Dev status", prod)  # a status chip, not a Dev tenant row
        self.assertNotIn("Account ID / UUID", prod)  # Dev tenant ids never appear on a production page
        for letter in "ABCD":
            self.assertNotIn(letter * 20, prod)

    def test_the_match_module_persists_nothing_and_reads_no_secret(self):
        text = Path(pm.__file__).read_text(encoding="utf-8")
        for forbidden in ("write_text", "open(", "json.dump", "os.environ", "password", "token"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
