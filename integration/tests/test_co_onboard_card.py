"""The "Onboard" card at the top of the review page (/onboard/CO-x).

The production CO page (/co/CO-x?env=prod) no longer carries this card (docs/50): see test_production_co_page.py.

Hermetic: no Salesforce, Leonardo, Redash or network. The card is read-only: the production row is a checklist with a
status chip per item and never a control; the only link is the existing Dev "Review & start" route, and only on the
desktop when the CO is Dev-startable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
import unittest
from unittest.mock import patch

from integration.onboarding import production_views as views
from integration.tests import test_dev_prod_areas as areas_module
from integration.tests import test_production_first_dashboard as base
import tools.serve_attended_open_onboardings_dashboard as dashboard

ITEM_LABELS = ["Production runner unlocked", "Production session", "Clone fresh under 6 h", "No duplicate in production",
               "Salesforce data valid", "Timing window", "One-CO create approval", "Dry run done", "Owner go"]
TODAY = date(2026, 10, 16)
FRESH = {"state": "fresh", "age_seconds": 7800, "reason": ""}
CLEAR = {"status": "not_in_production", "candidates": [], "tenants_checked": 5}
VALID = {"state": "valid", "item": {"expires_at": "2026-10-23T09:00:00Z"}}
CREATE_NONE = {"code": "locked", "steps_done": 0}


def checklist(**over):
    args = dict(vm=False, locked=True, lock_reason="production_sessions_flag_missing", session_ready=None, clone=FRESH, match=CLEAR,
                domain_count=1, uuid_state="empty", ce_route=True, start_iso="2026-10-18", today=TODAY, days_before=2,
                approval=VALID, create=CREATE_NONE)
    args.update(over)
    return {item["key"]: item for item in views.onboard_checklist(**args)}


class ChecklistTests(unittest.TestCase):
    def test_nine_items_in_order_with_labels(self):
        items = views.onboard_checklist(**{**dict(vm=False, locked=True, lock_reason=None, session_ready=None, clone=FRESH,
                                                    match=CLEAR, domain_count=1, uuid_state="empty", ce_route=True,
                                                    start_iso="2026-10-18", today=TODAY, days_before=2, approval=VALID,
                                                    create=CREATE_NONE)})
        self.assertEqual([item["label"] for item in items], ITEM_LABELS)
        self.assertTrue(all(item["status"] in ("locked", "ready", "blocked", "unavailable") for item in items))

    def test_lock_and_session(self):
        got = checklist()
        self.assertEqual(got["unlock"]["status"], "locked")
        self.assertIn("Production sessions are not enabled.", got["unlock"]["detail"])
        self.assertEqual(got["session"]["status"], "locked")
        self.assertEqual(checklist(vm=True)["session"]["status"], "unavailable")
        self.assertIn("unavailable on this server", checklist(vm=True)["session"]["detail"])
        unlocked = checklist(locked=False, lock_reason=None)
        self.assertEqual(unlocked["unlock"]["status"], "ready")
        self.assertEqual(unlocked["session"]["status"], "unavailable")  # nothing records a production session: never green
        self.assertEqual(checklist(locked=False, lock_reason=None, session_ready=True)["session"]["status"], "ready")

    def test_clone(self):
        self.assertEqual(checklist()["clone"]["status"], "ready")
        self.assertEqual(checklist(clone={"state": "stale", "age_seconds": 30000, "reason": ""})["clone"]["status"], "blocked")
        self.assertEqual(checklist(clone={"state": "unusable", "age_seconds": None, "reason": "inventory_snapshot_missing"})
                         ["clone"]["status"], "unavailable")
        self.assertEqual(checklist(clone=None)["clone"]["status"], "unavailable")

    def test_duplicate_validation(self):
        self.assertEqual(checklist()["duplicate"]["status"], "ready")
        dup = {"status": "exists_matches", "candidates": [{"name": "A", "live_paid": False}], "tenants_checked": 5}
        self.assertEqual(checklist(match=dup)["duplicate"]["status"], "blocked")
        self.assertEqual(checklist(match={"status": "clone_unavailable", "candidates": []})["duplicate"]["status"], "unavailable")
        self.assertEqual(checklist(match=None)["duplicate"]["status"], "unavailable")

    def test_salesforce_data(self):
        self.assertEqual(checklist()["data"]["status"], "ready")
        self.assertEqual(checklist(domain_count=2)["data"]["status"], "blocked")
        self.assertEqual(checklist(domain_count=0)["data"]["status"], "blocked")
        self.assertEqual(checklist(uuid_state="set")["data"]["status"], "blocked")
        self.assertEqual(checklist(uuid_state="unread")["data"]["status"], "unavailable")
        self.assertEqual(checklist(domain_count=None)["data"]["status"], "unavailable")
        self.assertEqual(checklist(ce_route=False, domain_count=3)["data"]["status"], "ready")

    def test_timing(self):
        self.assertEqual(checklist()["timing"]["status"], "ready")
        self.assertEqual(checklist(start_iso="2026-11-20")["timing"]["status"], "blocked")
        self.assertEqual(checklist(start_iso="")["timing"]["status"], "unavailable")

    def test_create_approval(self):
        for state, status in (("valid", "ready"), ("expired", "blocked"), ("consumed", "blocked"), ("none", "blocked"),
                              ("unreadable", "blocked"), ("unavailable", "unavailable")):
            self.assertEqual(checklist(approval={"state": state, "item": VALID["item"]})["approval"]["status"], status, state)
        self.assertIn("unavailable on this server", checklist(approval={"state": "unavailable", "item": None})["approval"]["detail"])
        self.assertEqual(checklist(approval=None)["approval"]["status"], "unavailable")

    def test_dry_run_and_owner_go(self):
        got = checklist()
        self.assertEqual((got["dry_run"]["status"], got["owner_go"]["status"]), ("locked", "locked"))
        unlocked = checklist(locked=False, lock_reason=None)
        self.assertEqual((unlocked["dry_run"]["status"], unlocked["owner_go"]["status"]), ("blocked", "blocked"))
        one = checklist(create={"code": "dry_run_passed", "steps_done": 1})
        self.assertEqual((one["dry_run"]["status"], one["owner_go"]["status"]), ("ready", "locked"))
        two = checklist(create={"code": "x", "steps_done": 2})
        self.assertEqual((two["dry_run"]["status"], two["owner_go"]["status"]), ("ready", "ready"))
        for code in ("unavailable", "unreadable"):
            bad = checklist(create={"code": code, "steps_done": 0})
            self.assertEqual((bad["dry_run"]["status"], bad["owner_go"]["status"]), ("unavailable", "unavailable"))
        self.assertEqual(checklist(create=None)["dry_run"]["status"], "unavailable")

    def test_details_are_bounded_plain_text(self):
        bad = checklist(clone={"state": "unusable", "age_seconds": None, "reason": "<b>" + "x" * 500})
        self.assertLessEqual(len(bad["clone"]["detail"]), 200)

    def test_chip_table_covers_every_status(self):
        self.assertEqual(set(views.CHECK_CHIPS), {"locked", "ready", "blocked", "unavailable"})
        self.assertEqual([views.CHECK_CHIPS[key][1] for key in ("locked", "ready", "blocked", "unavailable")],
                         ["Locked", "Ready", "Blocked", "Unavailable"])


ROW_READY = {"Name": "CO-0780", "Account_Name__c": "State Land Service", "Account_UUID__c": None,
             "Onboarding_Product__c": dashboard.CE_ROUTE_PRODUCT, "Onboarding_Type__c": dashboard.CE_ROUTE_TYPE,
             "Onboarding_Approval_Status__c": "Approved", "Onboarding_Stage__c": "New", "Email_Domains__c": "@vzd.example"}
ROW_NOT_READY = {**ROW_READY, "Onboarding_Approval_Status__c": "Pending", "Onboarding_Stage__c": "New"}


class CardBase(unittest.TestCase):
    def setUp(self):
        self.env = base.Env()
        self.addCleanup(self.env.tmp.cleanup)

    def review(self, row=ROW_READY, vm=False, runner=None, **kwargs):
        with base.patched(self.env, unavailable=vm, **kwargs), \
                patch.object(dashboard, "vm_mode", return_value=vm), \
                patch.object(dashboard, "load_runner_state", return_value=runner if runner is not None else {}):
            return dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=row)

    def pages(self, **kwargs):
        return {"review": self.review(**kwargs)}

    @staticmethod
    def card(page):
        start = page.index("<section class='card onboard-card'")
        return page[start:page.index("</section>", start)]


class CardPlacementTests(CardBase):
    def test_card_present_at_the_top_on_both_routes(self):
        for name, page in self.pages().items():
            self.assertIn("<h2 id='co-onboard-title' class='pill'>Onboard</h2>", page, name)
            self.assertEqual(page.count("onboard-card"), 1, name)
            # before every other card
            others = [m.start() for m in re.finditer(r"<section class='card", page) if m.start() != page.index("<section class='card onboard-card'")]
            self.assertTrue(all(page.index("<section class='card onboard-card'") < pos for pos in others), name)

    def test_two_labelled_target_rows(self):
        for name, page in self.pages().items():
            card = self.card(page)
            self.assertIn("Leonardo Development (test)", card, name)
            self.assertIn("Production BackOffice", card, name)
            self.assertLess(card.index("Leonardo Development (test)"), card.index("Production BackOffice"), name)

    def test_lock_sentence(self):
        for name, page in self.pages().items():
            self.assertIn("Production create stays locked until the owner unlocks the runner.", self.card(page), name)


class DevRowTests(CardBase):
    def test_button_present_exactly_when_dev_start_is_eligible(self):
        for name, page in self.pages(row=ROW_READY).items():
            card = self.card(page)
            self.assertEqual(card.count("Review &amp; start (Dev)"), 1, name)
            self.assertIn("<a class='btn' href='/co/CO-0780'>Review &amp; start (Dev)</a>", card, name)
        for name, page in self.pages(row=ROW_NOT_READY).items():
            card = self.card(page)
            self.assertNotIn("Review &amp; start", card, name)
            self.assertNotIn("class='btn", card, name)
            self.assertIn("Validate DealHub term", card, name)  # the exact reason of the existing queue logic

    def test_no_route_is_not_startable(self):
        row = {**ROW_READY, "Onboarding_Product__c": "Other", "Onboarding_Type__c": "Other"}
        for name, page in self.pages(row=row).items():
            card = self.card(page)
            self.assertNotIn("Review &amp; start", card, name)
            self.assertIn("Onboard manually", card, name)

    def test_unreadable_row_or_runner_record_is_never_startable(self):
        page = self.review(row=None)  # the production view always has a row; only the review page can lack one
        self.assertNotIn("Review &amp; start", self.card(page))
        self.assertIn("not verified", self.card(page))
        with base.patched(self.env), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "load_runner_state", side_effect=dashboard.RunnerStateUnavailable()):
            page = dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=ROW_READY)
        self.assertNotIn("Review &amp; start", self.card(page))
        self.assertIn("not verified", self.card(page))

    def test_a_recorded_run_is_not_startable(self):
        runner = {"CO-0780": {"result": "runner_crashed", "route": dashboard.CE_ENGINE}}
        for name, page in self.pages(runner=runner).items():
            self.assertNotIn("Review &amp; start", self.card(page), name)


class VmModeTests(CardBase):
    def test_vm_shows_the_desktop_text_and_no_dev_button(self):
        for name, page in self.pages(vm=True).items():
            card = self.card(page)
            self.assertIn("Onboarding runs from the desktop dashboard", card, name)
            self.assertIn("<code>http://127.0.0.1:8012</code>", card, name)
            self.assertNotIn("href='http://127.0.0.1:8012", card, name)
            self.assertNotIn("Review &amp; start", card, name)
            self.assertNotIn("class='btn", card, name)

    def test_desktop_does_not_show_the_vm_text(self):
        for name, page in self.pages().items():
            self.assertNotIn("Onboarding runs from the desktop dashboard", self.card(page), name)

    def test_unevaluable_items_say_unavailable_on_this_server_and_are_never_green(self):
        for name, page in self.pages(vm=True).items():
            card = self.card(page)
            for label in ("Production session", "One-CO create approval", "Dry run done", "Owner go"):
                item = self.item(card, label)
                self.assertIn("unavailable on this server", item, (name, label))
                self.assertNotIn("chip-ok", item, (name, label))

    @staticmethod
    def item(card, label):
        start = card.index("<span class='what'>" + label + "<")
        return card[card.rindex("<li>", 0, start):card.index("</li>", start)]


class ProductionRowTests(CardBase):
    def test_every_item_in_order_with_a_chip(self):
        for name, page in self.pages().items():
            card = self.card(page)
            row = card[card.index("Production BackOffice"):]
            labels = re.findall(r"<li><span class='what'>([^<]+)", row)
            self.assertEqual(labels, ITEM_LABELS, name)
            self.assertEqual(len(re.findall(r"<li>.*?</li>", row, flags=re.S)), 9, name)
            for item in re.findall(r"<li>.*?</li>", row, flags=re.S):
                self.assertRegex(item, r"<span class='chip chip-(ok|warn|bad|neutral)'>(Locked|Ready|Blocked|Unavailable)</span></li>$")
            self.assertIn("<ol class='checks'>", row, name)

    def test_locked_chips_and_reason(self):
        for name, page in self.pages().items():
            row = self.card(page)
            self.assertIn("Production sessions are not enabled.", row, name)
            self.assertIn("chip-warn'>Locked</span>", row, name)
            self.assertIn("chip-ok'>Ready</span>", row, name)  # a clone 2 h old is fresh

    def test_production_row_has_no_button_form_or_input(self):
        for name, page in self.pages().items():
            card = self.card(page)
            row = card[card.index("Production BackOffice"):]
            for tag in ("<button", "<form", "<input", "<a ", "<script"):
                self.assertNotIn(tag, row, (name, tag))

    def test_no_create_or_confirm_control_strings_in_the_pages(self):
        for name, page in self.pages().items():
            self.assertNotRegex(page, r"(?i)<(button|a)[^>]*>[^<]*(create in production|start onboarding|confirm|create)")
            self.assertEqual(base.controls(page, allow=("/attended/review-approve", "Approve this summary")), [
                ("btn-link", "<a class='btn' href='/co/CO-0780'>Review &amp; start (Dev)</a>")], name)
            self.assertNotIn("<form", self.card(page), name)
            self.assertNotIn("/attended/", self.card(page), name)
            self.assertNotIn("production-create", page.lower().replace("production create", ""), name)

    def test_lock_state_never_adds_a_control_even_when_unlocked_by_configuration(self):
        with patch.object(dashboard.readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            for name, page in self.pages(unlock=None).items():
                row = self.card(page)[self.card(page).index("Production BackOffice"):]
                self.assertNotIn("<button", row, name)
                self.assertNotIn("<form", row, name)
                self.assertNotIn("<a ", row, name)

    def test_blocked_items_show_blocked(self):
        row = {**ROW_READY, "Account_UUID__c": "a" * 32, "Email_Domains__c": "@a.example, @b.example"}
        page = self.review(row=row)
        card = self.card(page)
        self.assertIn("chip-bad'>Blocked</span>", card)
        self.assertNotIn("a" * 32, page)

    def test_create_approval_and_records_feed_the_chips(self):
        self.env.write_approvals(base.approval())
        self.env.write_record(base.record("CO-0780", "dry_run_passed"))
        card = self.card(self.review())
        approval = VmModeTests.item(card, "One-CO create approval")
        self.assertIn("chip-ok'>Ready</span>", approval)
        dry = VmModeTests.item(card, "Dry run done")
        self.assertIn("chip-ok'>Ready</span>", dry)
        go = VmModeTests.item(card, "Owner go")
        self.assertIn("chip-warn'>Locked</span>", go)

    def test_stale_clone_blocks_and_a_missing_clone_is_unavailable(self):
        self.env.now = base.CAPTURED + timedelta(hours=7)
        self.assertIn("chip-bad'>Blocked</span>", VmModeTests.item(self.card(self.review()), "Clone fresh under 6 h"))
        gone = base.Env(clone=False)
        self.addCleanup(gone.tmp.cleanup)
        self.env = gone
        self.assertIn("chip-neutral'>Unavailable</span>", VmModeTests.item(self.card(self.review()), "Clone fresh under 6 h"))


class EscapingTests(CardBase):
    def test_hostile_values_are_escaped(self):
        hostile = "<img src=x onerror=alert(1)>"
        row = {**ROW_READY, "Account_Name__c": hostile, "Onboarding_Approval_Status__c": hostile, "Onboarding_Stage__c": hostile,
               "Account_UUID__c": hostile}
        for name, page in self.pages(row=row).items():
            self.assertNotIn("<img src=x", self.card(page), name)
        with base.patched(self.env, unavailable=False), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "classify_queue_row", return_value=("review", "<b>" + hostile + "</b>", "you")):
            page = dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=ROW_READY)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", self.card(page))

    def test_clone_reason_is_escaped(self):
        with base.patched(self.env), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "_clone_status", return_value={"state": "unusable", "captured_at": "", "age_seconds": None,
                                                                          "reason": "<script>x</script>", "limit_seconds": 21600,
                                                                          "label": ""}):
            page = dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=ROW_READY)
        self.assertNotIn("<script>x", page)


class HandlerRouteTests(CardBase):
    def test_both_routes_serve_the_card_through_the_handler(self):
        with base.patched(self.env), patch.object(dashboard, "build_review_summary", return_value=(base.summary_for(), base.EXTRAS)), \
                patch.object(dashboard, "detail_row", return_value=dict(ROW_READY)), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            review = areas_module.get("/onboard/CO-0780").pages[0]
        self.assertEqual(review[0], 200)
        self.assertIn("co-onboard-title", review[1])
        with base.patched(self.env), patch.object(dashboard, "detail_row", return_value=dict(ROW_READY)), \
                patch.object(dashboard, "_match_of", return_value=base.match()), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            prod = areas_module.get("/co/CO-0780?env=prod").pages[0]
        self.assertEqual(prod[0], 200)
        self.assertNotIn("co-onboard-title", prod[1])   # docs/50: the simplified page has no Onboard card
        self.assertIn("co-before-title", prod[1])

    def test_the_card_adds_no_post_route_and_the_lock_holds(self):
        self.assertIs(dashboard.readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertFalse([route for route in dashboard.POST_ROUTES if "onboard-card" in route or route.startswith("/onboard")])


if __name__ == "__main__":
    unittest.main()
