"""Recent onboardings panel + daily history (owner request 2026-11-10): primary user from the clone, both regions, why
texts, the landing-page panel, the daily log, VM rendering."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import post_onboard_audit as audit
from integration.onboarding import production_create_record as records
from integration.onboarding import recent_onboardings as recent
from integration.tests.test_post_onboard_audit_2026_11_06 import FakeRecords, _write_create

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
CLONE_AT = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
EU_UUID = "0123456789abcdef0123456789abcdef"
US_UUID = "fedcba9876543210fedcba9876543210"
SECRETS = ("Secret Tenant", "secret.example", "a@secret.example")


def _tenant(uuid: str, **overrides):
    row = {"account_uuid": uuid, "account_name": "Secret Tenant", "account_domain": "secret.example",
           "alternate_domains": ["alt.secret.example"], "created": 1_000, "operator_assigned": True,
           "campaigns_timeout_hours": 90, "scan": {"status": "COMPLETED", "last_recon_scan_ms": 2_000},
           "primary_user": {"present": False}, "v2": {"ce_status": "DONE", "primary_user_present": True}}
    row.update(overrides)
    return row


def _payload(region: str, tenants, *, age: int = 600, data_at: datetime = CLONE_AT, sha: str | None = None):
    env, query = {"EU": ("prod-clone", 253), "US": ("prod-clone-us", 257)}[region]
    return {"environment": env, "source": {"query_id": query, "data_latest_modified": data_at.strftime("%Y-%m-%dT%H:%M:%SZ")},
            "captured_at": data_at.strftime("%Y-%m-%dT%H:%M:%SZ"), "age_seconds": age, "tenants": list(tenants),
            "rows_sha256": sha}


def _loader(payloads):
    def load(env):
        if env not in payloads:
            raise OSError("inventory_snapshot_missing")
        return payloads[env]
    return load


class RecentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.parent = Path(self.tmp.name)
        for patch in (mock.patch.object(audit.records, "list_records", FakeRecords.list_records),
                      mock.patch.object(audit.records, "load", FakeRecords.load)):
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        recent._INDEX_CACHE.clear()  # noqa: SLF001

    def clones(self, eu=None, us=None, **kw):
        payloads = {}
        if eu is not None:
            payloads["prod-clone"] = _payload("EU", eu, **kw)
        if us is not None:
            payloads["prod-clone-us"] = _payload("US", us)
        return recent.region_clones(_loader(payloads), NOW)


def _primary_done(parent, co):
    from integration.onboarding import case3_followup
    case3_followup.record_primary_user(parent, co, "user_created_verified", NOW)


class PrimaryUserRecordOnlyTests(unittest.TestCase):
    """2026-11-11 (CO-0844): Redash primaryUserPresent is True for every dashboard create (the owner's attended account is
    the tenant's primary user), so it never decides the customer's Admin user: only our own record does."""

    def _state(self, tenant, primary=(None, None), clone_at=CLONE_AT):
        checks = {c["key"]: c for c in audit.view("case3", None, tenant, clone_at, primary)["checks"]}
        return checks["primary_user"]["state"], checks["primary_user"]["source"]

    def test_the_clone_flag_never_makes_it_done(self):
        self.assertEqual(self._state(_tenant(EU_UUID)), ("open", None))  # primaryUserPresent True: still open
        absent = _tenant(EU_UUID, v2={"ce_status": "DONE", "primary_user_present": False})
        self.assertEqual(self._state(absent), ("open", None))
        self.assertEqual(self._state(None, clone_at=None), ("open", None))

    def test_only_a_done_record_counts(self):
        for result in ("user_created_verified", "user_already_present", "owner_confirmed_by_hand"):
            self.assertEqual(self._state(_tenant(EU_UUID), (result, NOW)), ("done", "record"))
        for result in ("add_rejected", "control_item_not_found", "user_created_unverified"):
            self.assertEqual(self._state(_tenant(EU_UUID), (result, NOW)), ("open", "record"))

    def test_ce_only_never_gets_the_check(self):
        checks = audit.view("ce_only", None, _tenant(EU_UUID), CLONE_AT)["checks"]
        self.assertNotIn("primary_user", {c["key"] for c in checks})

    def test_scan_gate_ignores_the_primary_user(self):
        absent = _tenant(EU_UUID, v2={"ce_status": "DONE", "primary_user_present": False})
        self.assertTrue(audit.view("case3", None, absent, CLONE_AT)["finished"])


class RegionTests(RecentTestCase):
    def test_us_lookup_after_eu(self):
        _write_create(self.parent, "CO-0901", audit.CASE3_ROUTE, NOW - timedelta(days=1), uuid=US_UUID)
        _primary_done(self.parent, "CO-0901")
        item = recent.recent(self.parent, self.clones(eu=[_tenant(EU_UUID)], us=[_tenant(US_UUID)]))[0]
        self.assertEqual(item["region"], "US")
        self.assertTrue(audit.all_done(item))
        self.assertEqual(item["clone_day"], "2026-11-09")

    def test_stale_region_gives_unknown_never_done(self):
        _write_create(self.parent, "CO-0901", audit.CASE3_ROUTE, NOW - timedelta(days=1))
        stale = self.clones(eu=[_tenant(EU_UUID)], data_at=NOW - timedelta(days=3))
        item = recent.recent(self.parent, stale)[0]
        self.assertEqual(item["region"], "EU")
        self.assertEqual({c["state"] for c in item["checks"] if c["key"] != "primary_user"}, {"unknown"})
        self.assertNotIn("done", {c["state"] for c in item["checks"]})
        self.assertIn("EU clone is stale", item["checks"][0]["why"])
        old_query = self.clones(eu=[_tenant(EU_UUID)], age=7 * 3600)
        self.assertFalse(old_query[0]["fresh"])

    def test_missing_us_clone_is_unknown_not_an_error(self):
        _write_create(self.parent, "CO-0901", audit.CE_ROUTE, NOW - timedelta(days=1), uuid=US_UUID)
        clones = self.clones(eu=[_tenant(EU_UUID)])
        self.assertEqual([(c["region"], c["reason"]) for c in clones], [("EU", ""), ("US", "unavailable")])
        item = recent.recent(self.parent, clones)[0]
        self.assertIsNone(item["region"])
        self.assertEqual({c["state"] for c in item["checks"]}, {"unknown"})
        self.assertIn("US clone unavailable", item["checks"][0]["why"])

    def test_wrong_region_snapshot_is_refused(self):
        payloads = {"prod-clone": _payload("US", [_tenant(EU_UUID)])}
        clones = recent.region_clones(_loader(payloads), NOW, regions=("EU",))
        self.assertEqual(clones[0]["reason"], "wrong_region")
        self.assertIsNone(clones[0]["index"])


class WhyTests(unittest.TestCase):
    def _why(self, route, tenant, live=None, key=None, primary=(None, None)):
        item = audit.view(route, live, tenant, CLONE_AT if tenant is not None else None, primary)
        check = next(c for c in item["checks"] if c["key"] == key)
        return recent.describe(check, live, tenant, CLONE_AT if tenant is not None else None, primary, "no data")

    def test_surface_scan_whys(self):
        running = _tenant(EU_UUID, scan={"status": "RUNNING", "last_recon_scan_ms": 2_000})
        self.assertEqual(self._why("case3", running, key="surface_scan"), ("Surface scan running", "running"))
        never = _tenant(EU_UUID, scan={})
        self.assertEqual(self._why("case3", never, key="surface_scan")[1], "no scan has started since the tenant was created")
        failed = _tenant(EU_UUID, scan={"status": "FAILED", "last_recon_scan_ms": int(CLONE_AT.timestamp() * 1000)})
        self.assertEqual(self._why("case3", failed, key="surface_scan")[1], "last scan FAILED on 2026-11-09")
        old = _tenant(EU_UUID, scan={"status": "COMPLETED", "last_recon_scan_ms": 500})
        self.assertEqual(self._why("case3", old, key="surface_scan")[1], "completed scan is older than the tenant")
        self.assertEqual(self._why("case3", None, key="surface_scan"), ("Surface scan not checked", "no data"))

    def test_ce_and_primary_and_operator_whys(self):
        pending = _tenant(EU_UUID, v2={"ce_status": "PENDING", "primary_user_present": False})
        self.assertEqual(self._why("ce_only", pending, key="ce_scan"), ("CE scan pending", "CE scan pending (clone 2026-11-09)"))
        none = _tenant(EU_UUID, v2={"primary_user_present": True})
        self.assertEqual(self._why("ce_only", none, key="ce_scan")[1], "no CE scan yet (clone 2026-11-09)")
        self.assertEqual(self._why("case3", pending, key="primary_user"),
                         ("Primary user missing",
                          "the customer's Admin user has not been created yet (Create primary user on the CO page)"))
        self.assertEqual(self._why("case3", pending, key="primary_user", primary=("control_item_not_found", NOW))[1],
                         "last attempt: control item not found (use Create primary user on the CO page)")
        lone = _tenant(EU_UUID, operator_assigned=False)
        self.assertEqual(self._why("ce_only", lone, key="operator")[1], "no Operator Account on the tenant")
        live = audit.log_line("CO-0901", NOW, "scan_running", {}, [{"campaign_type": audit.CE_CAMPAIGN, "status": "PENDING"}])
        self.assertEqual(self._why("ce_only", None, live, key="ce_scan"), ("CE scan pending", "CE scan pending"))

    def test_whys_hold_no_identity(self):
        tenant = _tenant(EU_UUID, scan={"status": "FAILED", "last_recon_scan_ms": 5}, operator_assigned=False,
                         v2={"ce_status": "PENDING", "primary_user_present": False}, campaigns_timeout_hours=24)
        for key in audit.CHECKS["case3"]:
            text = " ".join(self._why("case3", tenant, key=key))
            for secret in (*SECRETS, EU_UUID):
                self.assertNotIn(secret, text)


class PanelTests(RecentTestCase):
    def test_ten_newest_escaped_all_done_no_identity(self):
        for n in range(12):
            _write_create(self.parent, f"CO-09{n:02d}", audit.CE_ROUTE if n % 2 else audit.CASE3_ROUTE,
                          NOW - timedelta(days=40 - n))
            if not n % 2:
                _primary_done(self.parent, f"CO-09{n:02d}")
        items = recent.recent(self.parent, self.clones(eu=[_tenant(EU_UUID)]))
        self.assertEqual([i["co"] for i in items], [f"CO-09{n:02d}" for n in range(11, 1, -1)])
        html = recent.panel_html(items, NOW, {"CO-0911": "<b>Acme & Co</b>"})
        self.assertIn("Recent onboardings", html)
        self.assertEqual(html.count("<li>"), 10)
        self.assertEqual(html.count("All done"), 10)
        self.assertIn("href='/co/CO-0911?env=prod'", html)
        self.assertIn("&lt;b&gt;Acme &amp; Co&lt;/b&gt;", html)
        self.assertIn("CE-only", html)
        self.assertIn("Case 3", html)
        for secret in (*SECRETS, EU_UUID, "<script"):
            self.assertNotIn(secret, html)

    def test_open_items_and_unavailable(self):
        _write_create(self.parent, "CO-0901", audit.CASE3_ROUTE, NOW - timedelta(days=1))
        tenant = _tenant(EU_UUID, scan={"status": "RUNNING"}, v2={"ce_status": "DONE", "primary_user_present": False})
        html = recent.panel_html(recent.recent(self.parent, self.clones(eu=[tenant])), NOW)
        self.assertIn("Surface scan running", html)
        self.assertIn("Primary user missing", html)
        self.assertIn("Admin user has not been created yet", html)
        self.assertNotIn("All done", html)
        self.assertIn("Status unavailable", recent.panel_html(None, NOW))

    def test_closed_by_default_with_a_one_line_summary(self):
        # Owner 2026-11-10: keep the Onboarding page clean; one click shows the rows.
        _write_create(self.parent, "CO-0901", audit.CASE3_ROUTE, NOW - timedelta(days=1))
        _write_create(self.parent, "CO-0902", audit.CE_ROUTE, NOW - timedelta(days=2))
        open_tenant = _tenant(EU_UUID, scan={"status": "RUNNING"}, v2={"ce_status": "DONE", "primary_user_present": False})
        html = recent.panel_html(recent.recent(self.parent, self.clones(eu=[open_tenant])), NOW)
        self.assertIn("<details><summary>", html)
        self.assertNotIn("<details open", html)
        summary = html[html.index("<summary>"):html.index("</summary>")]
        self.assertIn("Recent onboardings", summary)
        self.assertIn("last 2", summary)
        self.assertIn("1 need attention", summary)
        self.assertIn("1 all done", summary)  # the CE-only CO: CE scan done and operator assigned (no primary-user check)
        self.assertNotIn("not fully checked", summary)
        unchecked = recent.panel_html([{"co": "CO-0903", "route": "ce_only", "checks": [
            {"key": "operator", "state": audit.UNKNOWN, "word": "Operator not checked"}]}], NOW)
        self.assertIn("1 not fully checked", unchecked[:unchecked.index("</summary>")])
        self.assertNotIn("CO-0901", summary)  # the rows are only inside the fold-out
        self.assertIn("CO-0901", html[html.index("</summary>"):])
        empty = recent.panel_html([], NOW)
        self.assertIn("no dashboard creates yet", empty)
        self.assertIn("<details><summary>", recent.panel_html(None, NOW))


class DailyLogTests(RecentTestCase):
    def test_one_line_per_co_per_day_and_changed_since_yesterday(self):
        _write_create(self.parent, "CO-0901", audit.CASE3_ROUTE, NOW - timedelta(days=2))
        open_tenant = _tenant(EU_UUID, v2={"ce_status": "PENDING", "primary_user_present": False})
        yesterday = NOW - timedelta(days=1)
        first = recent.recent(self.parent, self.clones(eu=[open_tenant]))
        self.assertEqual(recent.record_daily(self.parent, first, yesterday), 1)
        self.assertEqual(recent.record_daily(self.parent, first, yesterday + timedelta(hours=2)), 0)
        now_items = recent.recent(self.parent, self.clones(eu=[_tenant(EU_UUID)]))
        self.assertEqual(recent.record_daily(self.parent, now_items, NOW), 1)
        lines = recent.daily_lines(self.parent)
        self.assertEqual([(l["co_reference"], l["day"]) for l in lines], [("CO-0901", "2026-11-09"), ("CO-0901", "2026-11-10")])
        self.assertEqual(lines[0]["checks"]["primary_user"], {"state": "open", "source": None})
        self.assertEqual(lines[1]["region"], "EU")
        self.assertEqual(lines[1]["clone_day"], "2026-11-09")
        raw = (self.parent / records.RECORDS_SUBDIR / recent.DAILY_LOG).read_text(encoding="utf-8")
        for secret in (*SECRETS, EU_UUID):
            self.assertNotIn(secret, raw)
        self.assertEqual(set(json.loads(raw.splitlines()[0])), {"co_reference", "day", "route", "region", "clone_day", "checks"})
        html = recent.panel_html(now_items, NOW, history=lines)
        self.assertIn("changed since yesterday", html)
        same = recent.panel_html(first, NOW, history=lines[:1])
        self.assertNotIn("changed since", same)

    def test_allow_list(self):
        good = [{"key": "ce_scan", "state": "done", "source": "clone"}, {"key": "operator", "state": "open", "source": None}]
        recent.daily_line("CO-0901", "2026-11-10", "ce_only", "EU", "2026-11-09", good)
        bad_cases = [
            ("not-a-co", "2026-11-10", "ce_only", "EU", None, good),
            ("CO-0901", "today", "ce_only", "EU", None, good),
            ("CO-0901", "2026-11-10", "case_9", "EU", None, good),
            ("CO-0901", "2026-11-10", "ce_only", "secret.example", None, good),
            ("CO-0901", "2026-11-10", "ce_only", "EU", "Nov 9", good),
            ("CO-0901", "2026-11-10", "ce_only", "EU", None, [{**good[0], "state": "Secret Tenant"}, good[1]]),
            ("CO-0901", "2026-11-10", "ce_only", "EU", None, [{**good[0], "source": "a@secret.example"}, good[1]]),
            ("CO-0901", "2026-11-10", "ce_only", "EU", None, good[:1]),
            ("CO-0901", "2026-11-10", "ce_only", "EU", None, good + [{"key": "primary_user", "state": "open", "source": None}]),
        ]
        for case in bad_cases:
            with self.assertRaises(ValueError, msg=repr(case)):
                recent.daily_line(*case)

    def test_bad_lines_are_skipped(self):
        folder = self.parent / records.RECORDS_SUBDIR
        folder.mkdir(parents=True)
        (folder / recent.DAILY_LOG).write_text("not json\n" + json.dumps({"co_reference": "CO-0901", "day": "x"}) + "\n",
                                                encoding="utf-8")
        self.assertEqual(recent.daily_lines(self.parent), [])


class DashboardPanelTests(RecentTestCase):
    """The landing panel through the dashboard: desktop writes the daily line, the VM renders the snapshot and writes nothing."""

    def setUp(self):
        super().setUp()
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        self.d = dashboard
        _write_create(self.parent, "CO-0901", audit.CE_ROUTE, NOW - timedelta(days=1))
        self.d._RECENT_CACHE.update({"at": 0.0, "items": None, "parent": None})  # noqa: SLF001
        self.addCleanup(self.d._RECENT_CACHE.update, {"at": 0.0, "items": None, "parent": None})  # noqa: SLF001
        payloads = {"prod-clone": _payload("EU", [_tenant(EU_UUID)])}
        for patch in (mock.patch.object(self.d, "production_records_parent", lambda: self.parent),
                      mock.patch.object(self.d, "production_now", lambda: NOW),
                      mock.patch.object(self.d.inventory, "load_latest", lambda root, env, **kw: _loader(payloads)(env))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_desktop_panel_writes_one_daily_line(self):
        with mock.patch.object(self.d, "vm_mode", lambda: False), mock.patch.dict(self.d._RECENT_CACHE, {"write": True}):  # noqa: SLF001
            html = self.d._recent_panel({})  # noqa: SLF001
            self.d._recent_onboardings(fresh=True)  # noqa: SLF001  (the background loop's call)
        self.assertIn("CO-0901", html)
        self.assertIn("All done", html)
        self.assertEqual(len(recent.daily_lines(self.parent)), 1)

    def test_only_the_served_process_writes(self):
        """Imports and tests that render the page never write the history (the flag is set by the server's start)."""
        with mock.patch.object(self.d, "vm_mode", lambda: False):
            self.assertIn("CO-0901", self.d._recent_panel({}))  # noqa: SLF001
        self.assertFalse((self.parent / records.RECORDS_SUBDIR / recent.DAILY_LOG).exists())

    def test_vm_renders_without_writing(self):
        with mock.patch.object(self.d, "vm_mode", lambda: True):
            html = self.d._recent_panel()  # noqa: SLF001
        self.assertIn("CO-0901", html)
        self.assertNotIn("Status unavailable", html)
        self.assertFalse((self.parent / records.RECORDS_SUBDIR / recent.DAILY_LOG).exists())

    def test_errors_show_status_unavailable(self):
        with mock.patch.object(recent, "recent", side_effect=RuntimeError("boom")):
            html = self.d._recent_panel()  # noqa: SLF001
        self.assertIn("Status unavailable", html)


class CloneOperatorCountTests(unittest.TestCase):
    """2026-11-10: the clone's operator flag is usually None; the v2 operator_count (Redash operatorCount) decides instead."""

    def check(self, tenant):
        return audit.checks_from_clone(tenant)["operator"]

    def test_the_count_decides_when_the_flag_is_missing(self):
        self.assertEqual(self.check({"v2": {"operator_count": 1}}), audit.DONE)
        self.assertEqual(self.check({"v2": {"operator_count": 0}}), audit.OPEN)

    def test_no_flag_and_no_count_stays_unknown(self):
        self.assertEqual(self.check({"v2": {}}), audit.UNKNOWN)
        self.assertEqual(self.check({"v2": {"operator_count": "1"}}), audit.UNKNOWN)
        self.assertEqual(self.check({"v2": {"operator_count": -1}}), audit.UNKNOWN)

    def test_a_known_flag_wins_over_the_count(self):
        self.assertEqual(self.check({"operator_assigned": False, "v2": {"operator_count": 3}}), audit.OPEN)
        self.assertEqual(self.check({"operator_assigned": True, "v2": {"operator_count": 0}}), audit.DONE)

if __name__ == "__main__":
    unittest.main()
