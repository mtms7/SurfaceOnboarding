"""Owner decision 2026-10-29 (CO-0801, LISI Automotive): a CE-only CO may be onboarded before its licence start when the CSM
asked for it ("early activation"). The owner ticks one box next to the "Onboard in production" button; the box is recorded
on the one-CO approval (``early_activation: true``) and the runner reads it from that file only. It opens the timing gate
(``csm_request_recorded``) and, when no Active Pentera Core Plus term exists yet, lets the one Pending (renewal) term stand in.
The licence still starts on the run day and ends on the term's end. Hermetic: temporary folders and fakes only.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create_runner as live_runner
from integration.tests import test_production_create_ui as base
from integration.tests import test_production_dry_run as dry
import tools.attended_ce_only_playwright as runner

CO, REF, REV, NOW, OWNER, ROUTE = base.CO, base.REF, base.REV, base.NOW, base.OWNER, base.ROUTE
CASE3_ROUTE = approval.CASE3_ROUTE


def core_plus(status, start, end, product="Pentera Core Plus Commercial"):
    return {"Product_Full_Name__c": product, "DealHub_Status__c": status,
            "DealHub_Subscription_Start_Date__c": start, "DealHub_Subscription_End_Date__c": end}


class SubscriptionTests(base.unittest.TestCase):
    PENDING = core_plus("Pending", "2026-11-06", "2029-11-05")
    OTHER_ACTIVE = {"Product_Full_Name__c": "Pentera Core Software", "DealHub_Status__c": "Active",
                    "DealHub_Subscription_Start_Date__c": "2025-11-06", "DealHub_Subscription_End_Date__c": "2026-11-05"}

    def test_without_the_flag_a_pending_term_is_still_unavailable(self):
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            runner.select_ce_subscription([self.PENDING, self.OTHER_ACTIVE], active_only=True)

    def test_with_the_flag_the_one_pending_term_is_used(self):
        self.assertEqual(runner.select_ce_subscription([self.PENDING, self.OTHER_ACTIVE], active_only=True,
                                                       pending_if_no_active=True),
                         (date(2026, 11, 6), date(2029, 11, 5)))

    def test_an_active_term_always_wins_over_a_pending_one(self):
        active = core_plus("Active", "2025-01-01", "2026-12-31")
        self.assertEqual(runner.select_ce_subscription([active, self.PENDING], active_only=True, pending_if_no_active=True),
                         (date(2025, 1, 1), date(2026, 12, 31)))

    def test_pending_terms_that_disagree_or_none_at_all_fail_closed(self):
        other = core_plus("Pending", "2026-12-01", "2027-11-30")
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_ambiguous"):
            runner.select_ce_subscription([self.PENDING, other], active_only=True, pending_if_no_active=True)
        expired = core_plus("Expired", "2024-01-01", "2025-01-01")
        bulk = core_plus("Pending", "2026-11-06", "2029-11-05", product="Pentera Core Plus Bulk Additional")
        with self.assertRaisesRegex(RuntimeError, "ce_subscription_unavailable"):
            runner.select_ce_subscription([expired, bulk, self.OTHER_ACTIVE], active_only=True, pending_if_no_active=True)

    def test_the_licence_starts_on_the_run_day_and_ends_on_the_term_end(self):
        self.assertEqual(runner.ce_run_license_dates(date(2026, 11, 6), date(2029, 11, 5), date(2026, 10, 29)),
                         (date(2026, 10, 29), date(2029, 11, 5)))


class ApprovalTests(base.Base):
    def issue(self, ref, route=ROUTE, revision=REV, **kw):
        return approval.issue(self.approvals, ref, CO, route, revision, OWNER, frozenset({OWNER}), NOW, **kw)

    def records(self):
        return json.loads(self.approvals.read_text(encoding="utf-8"))

    def test_the_flag_replaces_a_live_approval_of_the_same_revision(self):
        record = self.issue(REF + "-EA", early_activation=True)
        self.assertIs(record["early_activation"], True)
        stored = self.records()
        self.assertIn("superseded_at", stored[REF])
        self.assertIs(stored[REF + "-EA"]["early_activation"], True)
        self.assertNotIn("early_activation", stored[REF])

    def test_a_flagged_approval_is_not_replaced_or_repeated(self):
        self.issue(REF + "-EA", early_activation=True)
        for kw in ({}, {"early_activation": True}):
            with self.assertRaisesRegex(approval.ApprovalRefused, "approval_already_active_for_co"):
                self.issue(REF + "-X", **kw)

    def test_only_true_and_only_ce_only(self):
        for value in ("1", 1, None):
            with self.assertRaisesRegex(approval.ApprovalRefused, "early_activation_not_allowed"):
                self.issue(REF + "-Y", early_activation=value)
        with self.assertRaisesRegex(approval.ApprovalRefused, "early_activation_not_allowed"):
            approval.issue(self.dir / "c3.json", "PROD-C3-0798-X", CO, CASE3_ROUTE, REV, OWNER, frozenset({OWNER}), NOW,
                           tenant_name="Example", early_activation=True)

    def test_a_hand_edited_flag_that_is_not_true_makes_the_file_unreadable(self):
        stored = self.records()
        stored[REF]["early_activation"] = "yes"
        self.approvals.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(approval.check(self.approvals, REF, CO, ROUTE, REV, NOW), "approval_unreadable")


class PageTests(base.Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.launched, self.issued = [], []

    def launch(self, ref, revision, env):
        self.launched.append(ref)
        return base.SimpleNamespace(poll=lambda: None)

    def issue(self, ref, revision, route, **kw):
        self.issued.append(kw)
        approval.issue(self.approvals, REF + ("-EA" if kw.get("early_activation") else "-N"), ref, route, revision, OWNER,
                       frozenset({OWNER}), NOW, **kw)
        return True

    def post(self, inputs, **extra):
        token = re.search(r"name='nonce' value='([^']+)'", ui.create_card_html(inputs)).group(1)
        return ui.handle_create_post({"reference": CO, "nonce": token, **extra}, inputs, self.launch, self.issue)

    def test_the_ce_only_card_offers_the_box_unticked(self):
        card = ui.create_card_html(self.inputs())
        self.assertIn("name='early_activation' value='1'", card)
        self.assertNotIn("checked", card)
        self.assertEqual(card.count("<form"), 1)

    def test_ticking_the_box_records_the_flag_then_launches_once(self):
        self.assertEqual(self.post(self.inputs(), early_activation="1"), "launched")
        self.assertEqual(self.issued, [{"early_activation": True}])
        self.assertEqual(self.launched, [CO])
        _reason, ctx = ui.gate(self.inputs())
        self.assertIs(ctx["approval"]["early_activation"], True)

    def test_without_the_box_nothing_is_reissued(self):
        self.assertEqual(self.post(self.inputs()), "launched")
        self.assertEqual(self.issued, [])

    def test_a_recorded_flag_is_shown_instead_of_the_box(self):
        approval.issue(self.approvals, REF + "-EA", CO, ROUTE, REV, OWNER, frozenset({OWNER}), NOW, early_activation=True)
        card = ui.create_card_html(self.inputs())
        self.assertIn("Early activation recorded", card)
        self.assertNotIn("name='early_activation'", card)

    def test_a_refused_reissue_launches_nothing(self):
        token = re.search(r"name='nonce' value='([^']+)'", ui.create_card_html(self.inputs())).group(1)
        result = ui.handle_create_post({"reference": CO, "nonce": token, "early_activation": "1"}, self.inputs(), self.launch,
                                       lambda *a, **k: False)
        self.assertEqual(result, "approval_not_issued")
        self.assertEqual(self.launched, [])

    def test_the_first_click_of_an_approved_co_records_the_flag_with_the_approval(self):
        stored = json.loads(self.approvals.read_text(encoding="utf-8"))
        stored[REF]["consumed_at"] = "2026-10-17T11:00:00Z"
        self.approvals.write_text(json.dumps(stored), encoding="utf-8")
        inputs = self.inputs(row=base.row(Onboarding_Approval_Status__c="Approved"))
        self.assertEqual(ui.gate(inputs)[0], "approval_consumed")
        stored[REF].pop("consumed_at")
        stored.pop(REF)
        self.approvals.write_text(json.dumps(stored), encoding="utf-8")
        self.assertIn("name='early_activation'", ui.create_card_html(inputs))
        self.assertEqual(self.post(inputs, issue_approval="1", early_activation="1"), "launched")
        self.assertEqual(self.issued, [{"early_activation": True}])

    def test_stop_texts_say_nothing_was_opened_and_name_the_box(self):
        for code in ("ce_subscription_unavailable", "timing_window_not_met"):
            text = ui.gate_text(code)
            self.assertIn("Nothing was opened or created", text)
            self.assertIn("early activation box", text)
        progress = {"steps": [("Signing in", "failed")], "done": False, "failed": "ce_subscription_unavailable",
                    "current": "Signing in", "percent": 5}
        html = ui.progress_card_html(progress)
        self.assertIn("Stopped: No Active Pentera Core Plus", html)
        self.assertNotIn(".:", html)
        self.assertNotIn("Signing in</span>", html)


class RunnerTests(live_runner.Base):
    FUTURE = dict(subscription_start=date(2026, 11, 6), subscription_end=date(2029, 11, 5))

    def flag(self, h):
        stored = json.loads(h.approval_path.read_text(encoding="utf-8"))
        stored[live_runner.REF]["early_activation"] = True
        h.approval_path.write_text(json.dumps(stored), encoding="utf-8")

    def test_a_future_start_is_refused_before_the_browser_without_the_flag(self):
        h = self.harness()
        self.assertEqual(h.go(source=dry.make_source(**self.FUTURE))["result"], "timing_window_not_met")
        self.assertEqual((h.opened, h.page.confirm_clicks), (0, 0))

    def test_the_recorded_flag_opens_the_timing_gate(self):
        h = self.harness()
        self.flag(h)
        result = h.go(source=dry.make_source(**self.FUTURE))["result"]
        self.assertNotEqual(result, "timing_window_not_met")
        self.assertEqual(h.page.confirm_clicks, 1, result)

    def test_the_runner_reads_the_flag_from_the_approval_only(self):
        source = runner.run_production_create.__code__.co_names + runner.run_production_create.__code__.co_consts
        text = repr(source)
        self.assertIn("early_activation", text)
        self.assertNotIn("SURFACE_EARLY_ACTIVATION", text)


if __name__ == "__main__":
    base.unittest.main()
