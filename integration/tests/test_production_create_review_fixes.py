"""Tests for the review fixes (docs/53): positive search control, launch ticket, pre-arm checks, strict readback expectations,
owner review of a not-clicked attempt, and the hardened UUID write. Fakes only."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from integration.onboarding import production_contract as contract
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import production_launch_ticket as ticket
from integration.tests import test_production_create_live as tl
from integration.tests import test_production_create_ui as tu
import tools.clear_production_attempt as clear

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
CO, REF, REV = "CO-0798", "PROD-CE-0798-20261017", "rev1"


class TicketTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_valid_ticket_is_consumed_once(self):
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + timedelta(seconds=5)), ("milton", ""))
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)

    def test_binding_expiry_and_garbage(self):
        for args in ((CO, "other", REF), (CO, REV, "PROD-CE-0001-20261017")):
            ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
            self.assertEqual(ticket.consume(self.dir, *args, NOW)[0], None)
        ticket.mint(self.dir, CO, REV, REF, "milton", NOW)
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW + ticket.TTL)[0], None)
        (self.dir / ticket.SUBDIR / (CO + ".json")).write_text("junk", encoding="utf-8")
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW)[0], None)
        with self.assertRaises(ValueError):
            ticket.mint(self.dir, "bad", REV, REF, "m", NOW)
        with self.assertRaises(ValueError):
            ticket.mint(self.dir, CO, REV, REF, "", NOW)

    def test_missing_ticket_refuses(self):
        self.assertEqual(ticket.consume(self.dir, CO, REV, REF, NOW), (None, "launch_ticket_invalid"))


def mini_page(guard, *, sw=0, pages=None):
    page = SimpleNamespace(url=tl.ORIGIN + "/x")
    page.evaluate = lambda js, arg=None: {"count": sw}
    page.context = SimpleNamespace(pages=[page] if pages is None else [page, *pages])
    return page


class PreArmTests(unittest.TestCase):
    def test_service_worker_popup_and_extra_production_page_refuse(self):
        guard = live.LiveRequestGuard()
        self.assertIsNone(live.pre_arm_problem(mini_page(guard), guard))
        self.assertEqual(live.pre_arm_problem(mini_page(guard, sw=1), guard), "service_worker_present")
        other = SimpleNamespace(url=tl.ORIGIN + "/y")
        self.assertEqual(live.pre_arm_problem(mini_page(guard, pages=[other]), guard), "extra_pages_open")
        self.assertIsNone(live.pre_arm_problem(mini_page(guard, pages=[SimpleNamespace(url="http://127.0.0.1:8765/")]), guard))
        guard._on_popup(None)
        self.assertEqual(live.pre_arm_problem(mini_page(guard), guard), "extra_pages_open")

    def test_popup_handler_is_installed_and_a_blocked_pre_arm_means_no_click(self):
        guard = live.LiveRequestGuard()
        events = []
        page = tl.MiniPage(guard)
        page.on = lambda event, handler: events.append(event)
        page.route = lambda *a: None
        page.evaluate = lambda js, arg=None: {"count": 1}
        page.context = SimpleNamespace(pages=[page])
        guard.install(page)
        self.assertIn("popup", events)
        clicks = live.LiveClickLog()
        self.assertEqual(live.click_confirm_once(page, guard, clicks), live.NOT_CLICKED)
        self.assertEqual((page.clicks, clicks.confirm_clicks, guard.armed), (0, 0, False))

    def test_scroll_precedes_the_hit_test_and_preflight_never_clicks(self):
        guard = live.LiveRequestGuard()
        order = []
        page = tl.MiniPage(guard)
        button = tl.MiniButton(page)
        page.get_by_role = lambda *a, **k: button
        button.scroll_into_view_if_needed = lambda: order.append("scroll")
        original = button.evaluate
        button.evaluate = lambda js: order.append("hit") or original(js)
        self.assertIsNone(live.confirm_preflight(page))
        self.assertEqual((order, page.clicks), (["scroll", "hit"], 0))
        for hit in (False, None):
            page.hit = hit
            self.assertEqual(live.confirm_preflight(page), "confirm_preflight_failed")


class ControlTests(unittest.TestCase):
    def test_clone_control_is_deterministic_live_and_unique(self):
        tenants = [{"account_name": "A Co", "account_domain": "a.example", "enabled": True},
                   {"account_name": "B Co", "account_domain": "b.example", "enabled": True},
                   {"account_name": "Dup", "account_domain": "d1.example", "enabled": True},
                   {"account_name": "Dup", "account_domain": "d2.example", "enabled": True},
                   {"account_name": "Gone", "account_domain": "g.example", "enabled": True, "is_deleted": True},
                   {"account_name": "Off", "account_domain": "o.example", "enabled": False}]
        first = live.clone_search_control(tenants)
        self.assertEqual(first, live.clone_search_control(list(reversed(tenants))))
        self.assertIn(first["name"], ("A Co", "B Co"))
        self.assertIsNone(live.clone_search_control([]))
        self.assertIsNone(live.clone_search_control("x"))

    def test_control_problem_table(self):
        control = {"name": "A Co", "domain": "a.example"}
        row = {"name": "a  co", "domain": "a.example"}
        ok = tl.reply([row])
        self.assertIsNone(live.control_problem(control, ok, ok))
        self.assertEqual(live.control_problem(None, ok, ok), "search_control_unavailable")
        self.assertEqual(live.control_problem(control, ok, tl.reply([])), "search_unproven")
        self.assertEqual(live.control_problem(control, tl.reply([]), ok), "search_unproven")
        self.assertEqual(live.control_problem(control, ok, {"status": 401}), "production_session_expired")
        clear_demo = tl.CLEAR_DEMO
        self.assertEqual(live.checked_duplicate_problem(control, (tl.reply([]), ok), tl.reply([]), tl.reply([]), clear_demo,
                                                         tl.NAME, tl.DOMAIN), "search_unproven")
        self.assertIsNone(live.checked_duplicate_problem(control, (ok, ok), tl.reply([]), tl.reply([]), clear_demo,
                                                          tl.NAME, tl.DOMAIN))
        self.assertEqual(live.checked_duplicate_problem(control, (ok, ok), tl.reply([]), tl.reply([row]), clear_demo,
                                                         tl.NAME, tl.DOMAIN), "duplicate_in_production")


class StrictContractTests(unittest.TestCase):
    INTENDED = {**tl.INTENDED, "license_start": date(2026, 10, 16), "organization_email": "m+x@pentera.io"}

    def row(self, **over):
        base = tl.contract_row(**{"accountLicense.startDate": "2026-10-16T09:00:00.000Z",
                                  "accountLicense.expirationDate": "2027-05-20T07:00:00.000Z", "mfaRequired": True,
                                  "organizationEmail": "m+x@pentera.io"})
        base.update(over)
        return base

    def test_exact_row_passes_and_each_deviation_is_named(self):
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        self.assertEqual(contract.compare_readback(expected, self.row())[0], [])
        for key, bad, name in (("mfaRequired", False, "mfaRequired"), ("organizationEmail", "x@pentera.io", "organizationEmail"),
                               ("accountLicense.expirationDate", "2027-05-21T07:00:00.000Z", "licenseExpirationDate"),
                               ("accountLicense.startDate", "2026-10-20T09:00:00.000Z", "licenseStartDate")):
            self.assertEqual(contract.compare_readback(expected, self.row(**{key: bad}))[0], [name], key)

    def test_start_day_tolerates_one_day_and_non_strict_is_unchanged(self):
        expected = contract.expected_readback_row(self.INTENDED, strict=True)
        self.assertEqual(contract.compare_readback(expected, self.row(**{"accountLicense.startDate": "2026-10-17T01:00:00.000Z"}))[0], [])
        self.assertNotIn("mfaRequired", contract.expected_readback_row(self.INTENDED))
        with self.assertRaises(ValueError):
            contract.expected_readback_row({**tl.INTENDED}, strict=True)


class OwnerReviewTests(tu.Base):
    OLD = "PROD-CE-0001-20261016"

    def seed(self, *kinds):
        for kind in kinds:
            data = {"started": {"outcome": "attempt_started"}, "refused": {"outcome": "refused", "refusal_reason": "confirm_not_clicked"},
                    "cleared": {"outcome": "owner_review_cleared"}, "unknown": {"outcome": "uncertain_submit"}}[kind]
            pcr.record(self.dir, {"co_reference": tu.CO, "approval_ref": self.OLD, "route": tu.ROUTE, **data}, tu.NOW)

    def test_cleared_not_clicked_no_longer_blocks_but_unknown_never_clears(self):
        self.unlocked()
        self.seed("started", "refused")
        self.assertEqual(ui.gate(self.inputs())[0], "prior_attempt_needs_review")
        self.seed("cleared")
        self.assertIsNone(ui.gate(self.inputs())[0])
        self.assertTrue(ui.review_cleared(ui.load_co_records(self.dir, tu.CO)))
        self.seed("unknown")
        self.assertEqual(ui.gate(self.inputs())[0], "prior_attempt_needs_review")

    def run_tool(self, argv, answer, tty=True):
        out = io.StringIO()
        code = clear.run(argv, parent=self.dir, now=tu.NOW, input_fn=lambda _p: answer, isatty=lambda: tty, out=out)
        return code, json.loads(out.getvalue())

    def test_tool_records_only_a_provably_not_clicked_attempt_after_the_typed_phrase(self):
        self.seed("started", "refused")
        args = ["--co", tu.CO, "--approval-ref", self.OLD]
        self.assertEqual(self.run_tool(args, "yes")[1]["reason"], "phrase_mismatch")
        self.assertEqual(self.run_tool(args, "CLEAR CO-0798", tty=False)[1]["reason"], "interactive_terminal_required")
        code, line = self.run_tool(args, "CLEAR CO-0798")
        self.assertEqual((code, line["result"]), (0, "recorded"))
        self.assertIn("owner_review_cleared", [r["outcome"] for r in ui.load_co_records(self.dir, tu.CO)])

    def test_tool_refuses_unknown_or_unfinished_attempts(self):
        self.seed("unknown")
        self.assertEqual(self.run_tool(["--co", tu.CO, "--approval-ref", self.OLD], "CLEAR CO-0798")[1]["reason"], "attempt_not_clearable")
        self.assertEqual(self.run_tool(["--co", tu.CO, "--approval-ref", "PROD-CE-0002-20261016"], "CLEAR CO-0798")[1]["reason"],
                         "attempt_not_clearable")
        self.assertEqual(self.run_tool(["--co", tu.CO], "x")[1]["reason"], "invalid_arguments")


class UuidHardeningTests(tu.UuidTests):
    def test_started_line_blocks_before_the_write_and_record_failure_is_reported(self):
        self.rows = [tu.row(), tu.row(Account_UUID__c=tu.UUID)]
        original = ui.record_uuid_write
        calls = []

        def failing(parent, co, uuid, result, now):
            calls.append(result)
            if result != "write_started":
                raise OSError("disk")
            return original(parent, co, uuid, result, now)
        ui.record_uuid_write = failing
        try:
            self.assertEqual(self.run_confirm(), "record_write_failed")
        finally:
            ui.record_uuid_write = original
        self.assertEqual(calls, ["write_started", "written_verified"])
        code, _ = ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), self.rows[0])
        self.assertEqual(code, "uuid_write_needs_review")  # not swallowed: the intent line keeps blocking

    def test_no_write_when_the_intent_cannot_be_recorded(self):
        original = ui.record_uuid_write
        ui.record_uuid_write = lambda *a: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.assertEqual(self.run_confirm(), "record_write_failed")
        finally:
            ui.record_uuid_write = original
        self.assertEqual(self.writes, [])

    def test_a_second_write_after_uncertain_needs_the_recorded_owner_review(self):
        self.rows = [tu.row(), tu.row(Account_UUID__c="f" * 32)]
        self.assertEqual(self.run_confirm(), "write_uncertain")
        self.assertEqual(ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())[0], "uuid_write_needs_review")
        out = io.StringIO()
        code = clear.run(["--co", tu.CO, "--uuid-write"], parent=self.dir, now=tu.NOW, input_fn=lambda _p: "CLEAR UUID CO-0798",
                         isatty=lambda: True, out=out)
        self.assertEqual(code, 0)
        self.assertEqual(ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())[0], "ready")

    def test_a_row_that_changes_between_review_and_write_refuses(self):
        code, view = ui.handle_uuid_review({"reference": tu.CO}, self.inputs(), tu.row())
        reads = iter([tu.row(), tu.row(Account_UUID__c="c" * 32)])
        result = ui.handle_uuid_confirm({"reference": tu.CO, "nonce": view["token"]}, self.inputs(), lambda: next(reads),
                                        lambda argv: self.writes.append(argv) or True, "alias")
        self.assertEqual(result, "uuid_conflict")
        self.assertEqual(self.writes, [])


class CreatePostTicketTests(tu.PostTests):
    def test_a_launch_mints_a_ticket_bound_to_the_operator_and_a_mint_failure_launches_nothing(self):
        values = self.values()
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launched")
        body = json.loads((self.dir / ticket.SUBDIR / (tu.CO + ".json")).read_text(encoding="utf-8"))
        self.assertEqual((body["operator"], body["revision"], body["approval_ref"]), (tu.OWNER, tu.REV, tu.REF))
        self.setUp()
        self.unlocked()
        self.launched.clear()
        values = self.values()
        original = ticket.mint
        ticket.mint = lambda *a, **k: (_ for _ in ()).throw(OSError("disk"))
        try:
            self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launch_failed")
        finally:
            ticket.mint = original
        self.assertEqual(self.launched, [])


if __name__ == "__main__":
    unittest.main()
