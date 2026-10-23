"""The "Production readiness" card of the production CO page (/onboard/CO-x), formerly the "Onboard" card.

Owner feedback 2026-10-18: the card is production/BackOffice only (the Dev queue lives under /dev) and every row follows
the same create-gate result as the Create card (``production_create_ui.page_state``). The production view
(/co/CO-x?env=prod) has no such card (docs/50): see test_production_co_page.py.

Hermetic: no Salesforce, Leonardo, Redash or network. The card is read-only: a checklist with a status chip per item and
never a control.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html import escape
import re
import unittest
from unittest.mock import patch

from integration.onboarding import production_views as views
from integration.tests import test_dev_prod_areas as areas_module
from integration.tests import test_production_first_dashboard as base
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.tests.locked_build import locked_create_build  # noqa: E402
unlocked_build = base.unlocked_build

ITEM_LABELS = ["Production runner unlocked", "Production session", "Clone fresh under 6 h", "No duplicate in production",
               "Salesforce data valid", "Timing window", "One-CO create approval", "Dry run done", "Owner go"]
TODAY = date(2026, 10, 16)
FRESH = {"state": "fresh", "age_seconds": 7800, "reason": ""}
CLEAR = {"status": "not_in_production", "candidates": [], "tenants_checked": 5}
VALID = {"state": "valid", "item": {"expires_at": "2026-10-23T09:00:00Z"}}
CREATE_NONE = {"code": "locked", "steps_done": 0}


LOCKED_GATE = {"phase": "locked", "reason": "production_runner_not_supported", "text": "The production runner is locked."}
PASSED_GATE = {"phase": "ready", "reason": None, "text": "Every gate passed."}


def gate(reason, text="Gate text."):
    return {"phase": "blocked", "reason": reason, "text": text}


def checklist(**over):
    args = dict(vm=False, locked=True, lock_reason="production_sessions_flag_missing", clone=FRESH, match=CLEAR,
                domain_count=1, uuid_state="empty", ce_route=True, start_iso="2026-10-18", today=TODAY, days_before=2,
                approval=VALID, create=CREATE_NONE, gate=LOCKED_GATE)
    args.update(over)
    return {item["key"]: item for item in views.onboard_checklist(**args)}


class ChecklistTests(unittest.TestCase):
    def test_nine_items_in_order_with_labels(self):
        items = views.onboard_checklist(**{**dict(vm=False, locked=True, lock_reason=None, clone=FRESH,
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
        unlocked = checklist(locked=False, lock_reason=None, gate=None)
        self.assertEqual(unlocked["unlock"]["status"], "ready")
        self.assertEqual(unlocked["session"]["status"], "unavailable")  # no gate result: never green
        # The session follows the real gate: unlocked by this CO's approval record, never by the process environment.
        for reason in ("approval_missing", "operator_not_owner", "production_locked"):
            got = checklist(locked=False, lock_reason=None, gate=gate(reason, "Why " + reason))
            self.assertEqual(got["session"]["status"], "blocked", reason)
            self.assertIn("Why " + reason, got["session"]["detail"])
        for reason in (None, "clone_stale", "dry_run_required"):
            got = checklist(locked=False, lock_reason=None, gate=gate(reason) if reason else PASSED_GATE,
                            session={"detail": "CO-0798 create verified at 2026-10-18 10:00 UTC."})
            self.assertEqual(got["session"]["status"], "ready", reason)
            self.assertIn("app.pentera.io", got["session"]["detail"])
            self.assertIn("CO-0798 create verified", got["session"]["detail"])

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

    def test_create_approval_follows_the_gate_when_unlocked(self):
        got = checklist(locked=False, lock_reason=None, gate=gate("approval_revision_drift", "Issue a new approval."))
        self.assertEqual(got["approval"]["status"], "blocked")
        self.assertEqual(got["approval"]["detail"], "Issue a new approval.")

    def test_create_approval(self):
        for state, status in (("valid", "ready"), ("expired", "blocked"), ("consumed", "blocked"), ("none", "blocked"),
                              ("unreadable", "blocked"), ("unavailable", "unavailable")):
            self.assertEqual(checklist(approval={"state": state, "item": VALID["item"]})["approval"]["status"], status, state)
        self.assertIn("unavailable on this server", checklist(approval={"state": "unavailable", "item": None})["approval"]["detail"])
        self.assertEqual(checklist(approval=None)["approval"]["status"], "unavailable")

    def test_dry_run_and_owner_go(self):
        got = checklist()
        self.assertEqual((got["dry_run"]["status"], got["owner_go"]["status"]), ("locked", "locked"))
        unlocked = checklist(locked=False, lock_reason=None, gate=gate("clone_stale", "Refresh the clone."))
        self.assertEqual((unlocked["dry_run"]["status"], unlocked["owner_go"]["status"]), ("blocked", "blocked"))
        self.assertIn("Refresh the clone.", unlocked["owner_go"]["detail"])
        passed = checklist(locked=False, lock_reason=None, gate=PASSED_GATE)
        self.assertEqual((passed["dry_run"]["status"], passed["owner_go"]["status"]), ("ready", "ready"))
        old = checklist(locked=False, lock_reason=None, gate=gate("dry_run_old", "Run it again."),
                        create={"code": "dry_run_passed", "steps_done": 1})
        self.assertEqual((old["dry_run"]["status"], old["dry_run"]["detail"]), ("blocked", "Run it again."))
        review = checklist(locked=False, lock_reason=None, gate=gate("prior_attempt_needs_review"))
        self.assertEqual(review["dry_run"]["status"], "ready")
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

    @staticmethod
    def item(card, label):
        start = card.index("<span class='what'>" + label + "<")
        return card[card.rindex("<li>", 0, start):card.index("</li>", start)]


class CardPlacementTests(CardBase):
    def test_card_right_after_the_create_card(self):
        for name, page in self.pages().items():
            self.assertIn("<h2 id='co-ready-title' class='pill'>Production readiness</h2>", page, name)
            self.assertEqual(page.count("onboard-card"), 1, name)
            sections = [m.start() for m in re.finditer(r"<section class='card", page)]
            ready = page.index("<section class='card onboard-card'")
            self.assertEqual(sections.index(ready), 1, name)          # second card: the create card is first
            self.assertLess(page.index("co-create-title"), ready, name)

    def test_production_only(self):
        for name, page in self.pages().items():
            card = self.card(page)
            for text in ("Leonardo", "(test)", "(Dev)", "Review &amp; start", "Production BackOffice</h3>",
                         "stays locked until the owner unlocks the runner"):
                self.assertNotIn(text, card, (name, text))

    @unlocked_build
    def test_the_closing_sentence_is_the_gate_text(self):
        page = self.review()
        self.assertIn(escape(dashboard.create_ui.gate_text("approval_missing")), self.card(page))


class DevContentTests(CardBase):
    def test_no_dev_start_button_even_when_dev_startable(self):
        for row in (ROW_READY, ROW_NOT_READY):
            page = self.review(row=row)
            self.assertNotIn("Review &amp; start", page)
            self.assertNotIn("href='/co/CO-0780'", self.card(page))


class VmModeTests(CardBase):
    def test_vm_shows_the_desktop_text_and_no_button(self):
        for name, page in self.pages(vm=True).items():
            card = self.card(page)
            self.assertIn("Onboarding runs from the desktop dashboard", card, name)
            self.assertIn("<code>http://127.0.0.1:8012</code>", card, name)
            self.assertNotIn("href='http://127.0.0.1:8012", card, name)
            self.assertNotIn("class='btn", card, name)

    def test_desktop_does_not_show_the_vm_text(self):
        for name, page in self.pages().items():
            self.assertNotIn("Onboarding runs from the desktop dashboard", self.card(page), name)

    def test_unevaluable_items_are_never_green_on_the_vm(self):
        for name, page in self.pages(vm=True).items():
            card = self.card(page)
            self.assertIn("unavailable on this server", self.item(card, "Production session"), name)
            for label in ("Production session", "One-CO create approval", "Dry run done", "Owner go"):
                self.assertNotIn("chip-ok", self.item(card, label), (name, label))


class ProductionRowTests(CardBase):
    def test_every_item_in_order_with_a_chip(self):
        for name, page in self.pages().items():
            card = self.card(page)
            labels = re.findall(r"<li><span class='what'>([^<]+)", card)
            self.assertEqual(labels, ITEM_LABELS, name)
            self.assertEqual(len(re.findall(r"<li>.*?</li>", card, flags=re.S)), 9, name)
            for item in re.findall(r"<li>.*?</li>", card, flags=re.S):
                self.assertRegex(item, r"<span class='chip chip-(ok|warn|bad|neutral)'>(Locked|Ready|Blocked|Unavailable)</span></li>$")
            self.assertIn("<ol class='checks'>", card, name)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_locked_chips_and_reason(self):
        for name, page in self.pages().items():
            card = self.card(page)
            self.assertIn(escape(views.lock_reason_text("production_runner_not_supported")), card, name)
            self.assertIn("chip-warn'>Locked</span>", card, name)
            self.assertIn("chip-ok'>Ready</span>", card, name)  # a clone 2 h old is fresh
            self.assertIn("Create locked", card, name)

    @unlocked_build
    def test_unlocked_build_without_an_approval_is_blocked_not_locked(self):
        card = self.card(self.review())
        self.assertNotIn("chip-warn'>Locked</span>", card)
        self.assertIn("chip-ok'>Ready</span>", self.item(card, "Production runner unlocked"))
        self.assertIn("chip-bad'>Blocked</span>", self.item(card, "Production session"))
        self.assertIn("Create blocked", card)

    def test_card_has_no_button_form_input_or_link(self):
        for name, page in self.pages().items():
            card = self.card(page)
            for tag in ("<button", "<form", "<input", "<a ", "<script"):
                self.assertNotIn(tag, card, (name, tag))

    def test_no_create_control_while_the_gate_is_closed(self):
        for name, page in self.pages().items():
            self.assertNotRegex(page, r"(?i)<(button|a)[^>]*>[^<]*(create in production|start onboarding|confirm|create)")
            self.assertEqual(base.controls(page, allow=("/attended/review-approve", "Approve this summary")), [], name)
            self.assertNotIn("/attended/", self.card(page), name)

    def test_blocked_items_show_blocked(self):
        row = {**ROW_READY, "Account_UUID__c": "a" * 32, "Email_Domains__c": "@a.example, @b.example"}
        page = self.review(row=row)
        card = self.card(page)
        self.assertIn("chip-bad'>Blocked</span>", card)
        self.assertNotIn("a" * 32, page)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_create_approval_and_records_feed_the_chips(self):
        self.env.write_approvals(base.approval())
        self.env.write_record(base.record("CO-0780", "dry_run_passed"))
        card = self.card(self.review())
        self.assertIn("chip-ok'>Ready</span>", self.item(card, "One-CO create approval"))
        self.assertIn("chip-ok'>Ready</span>", self.item(card, "Dry run done"))
        self.assertIn("chip-warn'>Locked</span>", self.item(card, "Owner go"))

    def test_stale_clone_blocks_and_a_missing_clone_is_unavailable(self):
        self.env.now = base.CAPTURED + timedelta(hours=7)
        self.assertIn("chip-bad'>Blocked</span>", self.item(self.card(self.review()), "Clone fresh under 6 h"))
        gone = base.Env(clone=False)
        self.addCleanup(gone.tmp.cleanup)
        self.env = gone
        self.assertIn("chip-neutral'>Unavailable</span>", self.item(self.card(self.review()), "Clone fresh under 6 h"))


class EscapingTests(CardBase):
    def test_hostile_values_are_escaped(self):
        hostile = "<img src=x onerror=alert(1)>"
        row = {**ROW_READY, "Account_Name__c": hostile, "Onboarding_Approval_Status__c": hostile, "Onboarding_Stage__c": hostile,
               "Account_UUID__c": hostile}
        for name, page in self.pages(row=row).items():
            self.assertNotIn("<img src=x", page, name)

    def test_clone_reason_is_escaped(self):
        with base.patched(self.env), patch.object(dashboard, "vm_mode", return_value=False), \
                patch.object(dashboard, "load_runner_state", return_value={}), \
                patch.object(dashboard, "_clone_status", return_value={"state": "unusable", "captured_at": "", "age_seconds": None,
                                                                          "reason": "<script>x</script>", "limit_seconds": 21600,
                                                                          "label": ""}):
            page = dashboard.page_onboard_review(base.summary_for(), base.EXTRAS, "", row=ROW_READY)
        self.assertNotIn("<script>x", page)


class HandlerRouteTests(CardBase):
    def test_both_routes_through_the_handler(self):
        with base.patched(self.env), patch.object(dashboard, "build_review_summary", return_value=(base.summary_for(), base.EXTRAS)), \
                patch.object(dashboard, "detail_row", return_value=dict(ROW_READY)), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            review = areas_module.get("/onboard/CO-0780").pages[0]
        self.assertEqual(review[0], 200)
        self.assertIn("co-ready-title", review[1])
        with base.patched(self.env), patch.object(dashboard, "detail_row", return_value=dict(ROW_READY)), \
                patch.object(dashboard, "_match_of", return_value=base.match()), \
                patch.object(dashboard, "load_runner_state", return_value={}):
            prod = areas_module.get("/co/CO-0780?env=prod").pages[0]
        self.assertEqual(prod[0], 200)
        self.assertNotIn("co-ready-title", prod[1])   # docs/50: the simplified page has no readiness card
        self.assertIn("co-before-title", prod[1])

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_the_card_adds_no_post_route_and_the_lock_holds(self):
        self.assertIs(dashboard.readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertFalse([route for route in dashboard.POST_ROUTES if "onboard-card" in route or route.startswith("/onboard")])


if __name__ == "__main__":
    unittest.main()
