"""Owner observations after CO-0814 (2026-11-03): a wrong Onboarding Comments date range ("2026-11-02 - 2029-22-01") must not
block when the DealHub subscription rows give the term, and the live create's duplicate checks must show in the progress bar.
Local only.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import tools.attended_ce_only_playwright as runner
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import production_views as views
from integration.onboarding import run_progress
from integration.tests.test_onboarding_sheet import sheet


class DealHubTermTests(unittest.TestCase):
    def test_wrong_comment_range_uses_the_dealhub_term(self):
        result = sheet(comments="2026-11-02 - 2029-22-01\nSeidor has 2 CE domains", dealhub_term=("2026-11-02", "2029-11-01"))
        self.assertEqual(result["state"], "ready")
        rows = {row["label"]: row for row in result["rows"]}
        self.assertEqual(rows["Expiration date"]["value"], "2029-11-01")
        self.assertIn("DealHub subscription", rows["Expiration date"]["note"])
        self.assertIn("wrong or missing", result["dates_note"])
        guide = views.production_co_guide(result, clone={"state": "fresh", "age_seconds": 60})
        self.assertIn("2026-11-02 - 2029-11-01 are used", guide["dates_note"])
        self.assertNotIn("Blocked: The DealHub date range", guide["headline"])

    def test_a_different_comment_range_is_named(self):
        result = sheet(dealhub_term=("2026-10-18", "2028-10-17"))
        self.assertEqual({row["label"]: row["value"] for row in result["rows"]}["Expiration date"], "2028-10-17")
        self.assertIn("Onboarding Comments show 2026-10-18 - 2027-10-17", result["dates_note"])

    def test_matching_comments_need_no_note(self):
        result = sheet(dealhub_term=("2026-10-18", "2027-10-17"))
        self.assertNotIn("dates_note", result)

    def test_without_a_term_the_comment_rule_stays(self):
        self.assertEqual(sheet(comments="2026-11-02 - 2029-22-01")["state"], "refused")
        self.assertEqual(sheet(comments="2026-11-02 - 2029-22-01", dealhub_term=("bad", "2029-11-01"))["state"], "refused")

    def test_the_create_plan_carries_the_term(self):
        self.assertIn('"term_start": source.subscription_start.isoformat()', Path(runner.__file__).read_text(encoding="utf-8"))


class CheckProgressTests(unittest.TestCase):
    def events(self, *pairs):
        return [{"t": "2026-11-03T21:35:00Z", "step": step, "outcome": outcome} for step, outcome in pairs]

    def test_states(self):
        rows = run_progress.production_create_checks(self.events(), running=True)
        self.assertEqual([state for _l, state in rows], ["active", "pending", "pending", "pending"])
        rows = run_progress.production_create_checks(self.events(("pcr_signed_in", "ok"), ("pcr_dup_early", "ok_3")),
                                                     running=True)
        self.assertEqual([state for _l, state in rows], ["done", "done", "active", "pending"])
        self.assertIn("3 searches, no match", rows[1][0])
        rows = run_progress.production_create_checks(
            self.events(("pcr_signed_in", "ok"), ("pcr_dup_early", "duplicate_in_production")), running=False)
        self.assertEqual([state for _l, state in rows], ["done", "failed", "pending", "pending"])
        self.assertIn("duplicate_in_production", rows[1][0])
        done = run_progress.production_create_checks(self.events(*[(step, "ok_3" if "dup" in step else "ok")
                                                                   for step, _l in run_progress.PRODUCTION_CREATE_CHECKS]),
                                                     running=False)
        self.assertEqual({state for _l, state in done}, {"done"})

    def test_the_milestones_are_accepted_and_recorded(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        path = folder / "progress.json"
        with mock.patch.object(runner, "PROGRESS_PATH", path):
            runner._pcr_note("CO-0814", "pcr_dup_early", "ok_3", lambda: datetime(2026, 11, 3, 21, 35, tzinfo=timezone.utc))
        self.assertEqual(run_progress.read_milestones(path, "CO-0814"),
                         [{"t": "2026-11-03T21:35:00Z", "step": "pcr_dup_early", "outcome": "ok_3"}])

    def test_card_lists_the_checks_under_the_first_step(self):
        progress = {"steps": [("Signing in", "active"), ("Creating", "pending")], "percent": 5, "current": "Signing in",
                    "done": False, "failed": None,
                    "checks": [("Signed in to BackOffice production", "done"),
                               ("Duplicate check in production: tenant name and every domain: 3 searches, no match", "done"),
                               ("Duplicate check again, right before the Add Account form opens", "active")]}
        html = create_ui.progress_card_html(progress)
        self.assertIn("3 searches, no match", html)
        self.assertLess(html.index("Signing in"), html.index("Signed in to BackOffice"))
        self.assertLess(html.index("right before the Add Account form opens (running)"), html.index("Creating"))

    def test_the_runner_records_every_check(self):
        text = Path(runner.__file__).read_text(encoding="utf-8")
        for step, _label in run_progress.PRODUCTION_CREATE_CHECKS:
            self.assertIn('_pcr_note(reference, "' + step + '"', text)


if __name__ == "__main__":
    unittest.main()
