"""Owner request 2026-11-05 (CO-0841): after a verified Case 3 create no Surface scan had started (the Add Account form has no
"Scan now" control once an interval is chosen; Edit Account saves scanNow false). The owner started it from the tenant's row
menu "Scan now" (one POST /account/{id}/campaign/executeReconCampaign, body {}; the row then showed RUNNING). The run now does
the same once after the post-create Edit Account, and the dashboard says "Action needed in BackOffice" when it could not.

Hermetic: fake pages, fake replies, temporary record folders; nothing reaches BackOffice, Salesforce or Redash.
"""

from __future__ import annotations

import contextlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_case3_scan_now as scan_now
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as records
import tools.attended_ce_only_playwright as runner

ACCOUNT_ID = "6aeceab7dd5a84118e8a2961"
OTHER_ID = "64f5c28660847c632262f501"
NAME, DOMAIN = "Los Angeles Department of Water & Power", "ladwp.example"
INTENDED = {"tenant_name": NAME, "primary_domain": DOMAIN}
SCAN_URL = "https://app.pentera.io/api/v1/backoffice/account/{}/campaign/executeReconCampaign"
CREATED = 1793911479041


def row(**over):
    base = {"id": ACCOUNT_ID, "accountName": NAME, "accountDomain": DOMAIN, "_created": CREATED, "lastReconScan": None,
            "lastScanStatusEnum": None}
    base.update(over)
    return base


class RuleTests(unittest.TestCase):
    def test_started(self):
        self.assertFalse(scan_now.started(row()))
        for status in ("RUNNING", "COMPLETED", "INCOMPLETE"):
            self.assertTrue(scan_now.started(row(lastScanStatusEnum=status)), status)
        self.assertTrue(scan_now.started(row(lastReconScan=CREATED + 1)))
        self.assertFalse(scan_now.started(row(lastReconScan=CREATED - 1)))  # a value from before the create never counts
        self.assertFalse(scan_now.started(row(lastScanStatusEnum="QUEUED_SOMEHOW")))
        self.assertFalse(scan_now.started(None))

    def test_the_exact_path_on_the_production_origin_only(self):
        self.assertEqual(live.scan_now_request_id("POST", SCAN_URL.format(ACCOUNT_ID)), ACCOUNT_ID)
        self.assertEqual(live.scan_now_request_id("POST", SCAN_URL.format(ACCOUNT_ID) + "?x=1"), ACCOUNT_ID)
        self.assertIsNone(live.scan_now_request_id("GET", SCAN_URL.format(ACCOUNT_ID)))
        self.assertIsNone(live.scan_now_request_id("POST", SCAN_URL.format(ACCOUNT_ID).replace("app.pentera.io", "evil.example")))
        self.assertIsNone(live.scan_now_request_id("POST", SCAN_URL.format(ACCOUNT_ID) + "/more"))

    def test_menu_tokens(self):
        self.assertTrue(scan_now.menu_token_allowed("Button-Grid_ScanNow"))
        for bad in ("Button-Grid_Delete", "Button-Grid_DisableAccount", "Button-Grid_ResetPassword", "x", None, "Button-Grid_"):
            self.assertFalse(scan_now.menu_token_allowed(bad), bad)

    def test_response(self):
        self.assertIsNone(scan_now.response_problem("POST", ACCOUNT_ID, 200, ACCOUNT_ID))
        self.assertEqual(scan_now.response_problem("POST", OTHER_ID, 200, ACCOUNT_ID), "scan_now_id_mismatch")
        self.assertEqual(scan_now.response_problem("POST", ACCOUNT_ID, 403, ACCOUNT_ID), "scan_now_rejected")

    def test_action_needed_only_for_creates_with_the_automatic_edit(self):
        self.assertIsNone(scan_now.action_needed(None, None))                       # old creates: nothing new shown
        self.assertEqual(scan_now.action_needed(None, "edit_verified"), scan_now.ACTION_TEXT)
        self.assertEqual(scan_now.action_needed("scan_now_no_signal", "edit_verified"), scan_now.ACTION_TEXT)
        for ok in (scan_now.VERIFIED, scan_now.ALREADY):
            self.assertIsNone(scan_now.action_needed(ok, "edit_verified"))


class Route:
    def __init__(self, url, method="POST"):
        self.request = SimpleNamespace(method=method, url=url, post_data=None)
        self.done = None

    def abort(self):
        self.done = "abort"

    def continue_(self):
        self.done = "continue"


class GuardTests(unittest.TestCase):
    def send(self, g, account_id=ACCOUNT_ID):
        route = Route(SCAN_URL.format(account_id))
        g.handle(route)
        return route.done

    def test_aborted_unless_armed_and_only_one_for_the_armed_id(self):
        g = live.LiveRequestGuard()
        g.create_used = True
        self.assertEqual(self.send(g), "abort")
        g.arm_scan_now(ACCOUNT_ID)
        self.assertEqual(self.send(g, OTHER_ID), "abort")
        self.assertEqual(self.send(g), "continue")
        self.assertEqual(self.send(g), "abort")
        self.assertEqual(g.scan_blocked, 3)

    def test_never_before_the_create_and_never_in_a_renewal(self):
        g = live.LiveRequestGuard()
        g.arm_scan_now(ACCOUNT_ID)
        self.assertEqual(self.send(g), "abort")
        renewal = live.RenewalEditGuard()
        renewal.create_used = True
        renewal.arm_scan_now(ACCOUNT_ID)
        self.assertEqual(self.send(renewal), "abort")

    def test_an_edit_arm_does_not_open_the_scan(self):
        g = live.LiveRequestGuard()
        g.create_used = True
        g.arm_edit(ACCOUNT_ID)
        self.assertEqual(self.send(g), "abort")


class Item:
    def __init__(self, page, token):
        self.page, self.token = page, token

    def click(self, timeout=None):
        self.page.clicks.append(self.token)
        if self.page.fire:
            self.page.fired.append(SimpleNamespace(request=SimpleNamespace(method="POST", url=SCAN_URL.format(self.page.fire)),
                                                   url=SCAN_URL.format(self.page.fire), status=self.page.status))

    def get_attribute(self, name):
        return self.token


class Items:
    def __init__(self, page, matching):
        self.page, self.matching = page, matching

    def count(self):
        return len(self.matching)

    @property
    def first(self):
        return Item(self.page, self.matching[0])

    def filter(self, has_text=None):
        return self

    def evaluate_all(self, _script):
        return list(self.page.tokens)


class Simple:
    def __init__(self, page):
        self.page = page

    def filter(self, has_text=None):
        return self

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append("row-or-menu")


class Page:
    """Row menu items by data-am token with their text; one scan request fires on the click when ``fire`` is an id."""

    def __init__(self, items, fire=ACCOUNT_ID, status=200):
        self.items, self.tokens = items, [token for token, _text in items]
        self.fire, self.status = fire, status
        self.clicks, self.fired, self.keys = [], [], []
        self.keyboard = SimpleNamespace(press=self.keys.append)

    def locator(self, selector):
        if selector == runner.ROW_MENU_ITEMS_SELECTOR:
            page = self

            class Menu(Items):
                def filter(self, has_text=None):
                    return Items(page, [token for token, text in page.items if has_text.search(text)])
            return Menu(self, self.tokens)
        return Simple(self)

    def wait_for_timeout(self, _ms):
        pass

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=None):
        holder = SimpleNamespace()
        yield holder
        found = [r for r in self.fired if predicate(r)]
        if not found:
            raise TimeoutError("no response")
        holder.value = found[0]


class Guard:
    def __init__(self):
        self.armed = []
        self.scan_blocked = 0

    def arm_scan_now(self, account_id):
        self.armed.append(account_id)

    def disarm_scan_now(self):
        self.armed.append("off")


class RunnerTests(unittest.TestCase):
    MENU = [("Button-Grid_Details", "Details"), ("Button-Grid_Edit", "Edit"), ("Button-Grid_ScanNow", "Scan now")]

    def run_scan(self, page, replies):
        reader = SimpleNamespace(readback=mock.Mock(side_effect=[{"status": 200, "rows": [r]} for r in replies]))
        recorded, diag, guard = [], {}, Guard()
        with mock.patch.object(runner, "_pdr_reader", return_value=reader):
            result = runner._pcr_case3_scan_now(page, guard, INTENDED, ACCOUNT_ID, recorded.append, diag)
        return result, recorded, diag, guard

    def test_clicks_scan_now_once_and_reads_it_back_running(self):
        page = Page(self.MENU)
        result, recorded, diag, guard = self.run_scan(page, [row(), row(), row(lastScanStatusEnum="RUNNING")])
        self.assertEqual(result, scan_now.VERIFIED)
        self.assertEqual(page.clicks.count("Button-Grid_ScanNow"), 1)
        self.assertNotIn("Button-Grid_Edit", page.clicks)
        self.assertEqual(recorded, ["write_started"])
        self.assertEqual(guard.armed, [ACCOUNT_ID, "off"])
        self.assertEqual(diag["menu_item"], "Button-Grid_ScanNow")

    def test_nothing_is_clicked_when_a_scan_already_runs(self):
        page = Page(self.MENU)
        result, recorded, _diag, guard = self.run_scan(page, [row(lastScanStatusEnum="RUNNING")])
        self.assertEqual(result, scan_now.ALREADY)
        self.assertEqual((page.clicks, recorded, guard.armed), ([], [], []))

    def test_a_missing_item_names_the_menu_tokens_and_clicks_nothing(self):
        page = Page(self.MENU[:2])
        result, recorded, diag, _guard = self.run_scan(page, [row()])
        self.assertEqual(result, "scan_now_item_not_found")
        self.assertEqual(diag["menu_items"], "Button-Grid_Details,Button-Grid_Edit")
        self.assertEqual(recorded, [])
        self.assertEqual(page.keys, ["Escape"])

    def test_a_destructive_token_is_never_clicked_whatever_its_text(self):
        page = Page([("Button-Grid_DeleteAccount", "Scan now")])
        result, _recorded, _diag, _guard = self.run_scan(page, [row()])
        self.assertEqual(result, "scan_now_item_not_allowed")
        self.assertNotIn("Button-Grid_DeleteAccount", page.clicks)

    def test_no_request_or_a_wrong_reply_is_recorded_not_retried(self):
        page = Page(self.MENU, fire=None)
        self.assertEqual(self.run_scan(page, [row()])[0], "scan_now_no_signal")
        self.assertEqual(page.clicks.count("Button-Grid_ScanNow"), 1)
        page = Page(self.MENU, status=500)
        self.assertEqual(self.run_scan(page, [row()])[0], "scan_now_rejected")

    def test_not_seen_running_is_unverified(self):
        page = Page(self.MENU)
        result = self.run_scan(page, [row()] * (1 + runner.PCR_SCAN_NOW_REREADS))[0]
        self.assertEqual(result, "scan_now_unverified")

    def test_another_tenant_row_stops_before_any_click(self):
        page = Page(self.MENU)
        result, _recorded, _diag, _guard = self.run_scan(page, [row(id=OTHER_ID)])
        self.assertEqual(result, "scan_now_row_not_found")
        self.assertEqual(page.clicks, [])


class StepTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def lines(self):
        path = self.folder / records.RECORDS_SUBDIR / fu.WRITES_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def step(self, edit_result, account_id=ACCOUNT_ID, result="scan_now_verified"):
        stamp = lambda: __import__("datetime").datetime(2026, 11, 5, 21, 0, tzinfo=__import__("datetime").timezone.utc)
        with mock.patch.object(runner, "_pcr_case3_scan_now", return_value=result) as inner:
            out = runner._pcr_case3_scan_now_step(object(), object(), self.folder, "CO-0841", INTENDED,
                                                  {"account_id": account_id}, edit_result, stamp)
        return out, inner

    def test_runs_after_the_edit_and_records_the_result(self):
        out, inner = self.step("edit_verified")
        self.assertEqual(out, "scan_now_verified")
        inner.assert_called_once()
        self.assertEqual(self.lines()[-1]["step"], scan_now.STEP)
        self.assertEqual(self.lines()[-1]["result"], "scan_now_verified")

    def test_skipped_while_the_edit_form_may_be_open(self):
        out, inner = self.step("edit_form_unavailable_form_left_open")
        self.assertEqual(out, "scan_now_skipped_form_open")
        inner.assert_not_called()

    def test_an_unknown_account_id_never_clicks(self):
        out, inner = self.step("edit_verified", account_id=None)
        self.assertEqual(out, "scan_now_account_id_unknown")
        inner.assert_not_called()

    def test_the_run_calls_the_step_after_the_edit(self):
        source = Path(runner.__file__).read_text(encoding="utf-8")
        edit_at = source.index("edit_result = _pcr_case3_edit_step(")
        self.assertGreater(source.index("_pcr_case3_scan_now_step(page, guard, parent, reference", edit_at), edit_at)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())

    def write(self, step, result):
        fu._record(self.folder, "CO-0841", step, result, __import__("datetime").datetime(2026, 11, 5, tzinfo=__import__("datetime").timezone.utc))

    def test_co_0841_shows_both_actions_until_the_clone_shows_them_done(self):
        self.write("bo_edit", "edit_form_unavailable_form_left_open")
        items = fu.action_items(self.folder, "CO-0841", tenant=None)
        self.assertEqual(len(items), 2)
        self.assertIn("Edit Account", items[0])
        self.assertIn("Scan now", items[1])
        scanned = {"created": CREATED, "scan": {"status": "RUNNING", "last_recon_scan_ms": CREATED + 5},
                   "campaigns_timeout_hours": 90, "operator_assigned": False}
        self.assertEqual(fu.action_items(self.folder, "CO-0841", tenant=scanned), [items[0]])  # operator still missing
        done = {**scanned, "operator_assigned": True}
        self.assertEqual(fu.action_items(self.folder, "CO-0841", tenant=done), [])

    def test_a_verified_run_needs_nothing(self):
        self.write("bo_edit", "edit_verified")
        self.write(scan_now.STEP, scan_now.VERIFIED)
        self.assertEqual(fu.action_items(self.folder, "CO-0841", tenant=None), [])

    def test_old_creates_show_nothing_new(self):
        self.assertEqual(fu.action_items(self.folder, "CO-0802", tenant=None), [])

    def test_the_card_says_action_needed(self):
        self.write("ids", "written_verified")
        self.write("stage", "written_verified")
        self.write("bo_edit", "edit_verified")
        self.write(scan_now.STEP, "scan_now_item_not_found")
        with mock.patch.object(fu, "clone_tenant", return_value=None):
            html = fu.followup_card_html(self.folder, "CO-0841")
        self.assertIn("Action needed in BackOffice:", html)
        self.assertIn("Scan now", html)

    def test_the_queue_shows_the_chip(self):
        source = Path(runner.__file__).resolve().parents[0].joinpath("serve_attended_open_onboardings_dashboard.py").read_text(
            encoding="utf-8")
        self.assertIn('_chip("chip-warn", "Action needed in BackOffice")', source)


if __name__ == "__main__":
    unittest.main()
