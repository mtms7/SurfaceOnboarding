"""Owner request 2026-10-30 (CO-0831): the early-activation box is an exception for starting BEFORE the licence window, so it
is offered only while the Start-minus-2-days window is not open yet. A CO whose window is already open (CO-0831, start
2026-06-16) shows no box; a closed window (CO-0801, Pending term starting 2026-11-06) or an undetermined start still offers
it, and a flag already recorded on the approval is always shown. Hermetic: temporary folders and fakes only.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create_ui as ui
from integration.tests import test_production_create_ui as base
import tools.serve_attended_open_onboardings_dashboard as dashboard

CO, REF, REV, NOW, OWNER, ROUTE = base.CO, base.REF, base.REV, base.NOW, base.OWNER, base.ROUTE
BOX = "name='early_activation'"


class CardTests(base.Base):
    def setUp(self):
        super().setUp()
        self.unlocked()

    def test_an_open_window_shows_no_box_but_keeps_the_button(self):
        card = ui.create_card_html(self.inputs(early_offer=False))
        self.assertNotIn(BOX, card)
        self.assertNotIn("Early activation", card)
        self.assertIn("Onboard in production", card)

    def test_a_closed_or_unknown_window_offers_the_box(self):
        self.assertIn(BOX, ui.create_card_html(self.inputs()))
        self.assertIn(BOX, ui.create_card_html(self.inputs(early_offer=True)))

    def test_a_recorded_flag_is_shown_even_when_the_window_is_open(self):
        approval.issue(self.approvals, REF + "-EA", CO, ROUTE, REV, OWNER, frozenset({OWNER}), NOW, early_activation=True)
        card = ui.create_card_html(self.inputs(early_offer=False))
        self.assertIn("Early activation recorded", card)
        self.assertNotIn(BOX, card)

    def test_the_first_click_card_of_an_approved_co_follows_the_same_rule(self):
        self.approvals.unlink()
        row = base.row(Onboarding_Approval_Status__c="Approved")
        self.assertNotIn(BOX, ui.create_card_html(self.inputs(row=row, early_offer=False)))
        self.assertIn(BOX, ui.create_card_html(self.inputs(row=row)))

    def test_other_routes_never_show_it(self):
        self.assertEqual(ui._early_state("case_3_combined_baseline", None, True), "")


class DashboardTests(base.unittest.TestCase):
    def card_inputs(self, start, today=date(2026, 10, 30)):
        inputs = base.SimpleNamespace(early_offer=True)
        with mock.patch.object(dashboard, "_create_ui_inputs", return_value=inputs), \
                mock.patch.object(dashboard.dataclasses, "replace", side_effect=lambda obj, **kw: {**vars(obj), **kw}), \
                mock.patch.object(dashboard, "_prod_view_start", return_value=start), \
                mock.patch.object(dashboard, "production_now", return_value=base.NOW.replace(year=today.year, month=today.month,
                                                                                         day=today.day)):
            result = dashboard._card_inputs(CO, None)
        return result["early_offer"] if isinstance(result, dict) else result.early_offer

    def test_the_window_decides_the_offer(self):
        self.assertFalse(self.card_inputs("2026-06-16"))  # CO-0831: open long ago
        self.assertFalse(self.card_inputs("2026-11-01"))  # opens 10-30: open today
        self.assertTrue(self.card_inputs("2026-11-06"))   # CO-0801's Pending term: opens 11-04
        self.assertTrue(self.card_inputs(None))           # unknown start: offered, the gate decides

    def test_a_read_failure_keeps_the_offer(self):
        with mock.patch.object(dashboard, "_create_ui_inputs", return_value=base.SimpleNamespace(early_offer=True)), \
                mock.patch.object(dashboard, "_prod_view_start", side_effect=RuntimeError("x")):
            self.assertTrue(dashboard._card_inputs(CO, None).early_offer)

    def test_the_approvals_page_has_a_chip_for_every_approval_state(self):
        # 2026-10-30: a superseded approval (early activation, CO-0801) made /prod/approvals fail with KeyError.
        self.assertLessEqual({"valid", "expired", "consumed", "superseded"}, set(dashboard._APPROVAL_STATE_CHIPS))

    def test_the_real_inputs_carry_the_field(self):
        self.assertIn("early_offer", {f.name for f in dataclasses.fields(ui.Inputs)})


if __name__ == "__main__":
    base.unittest.main()
