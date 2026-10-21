"""The Case 3 production dry run records real milestones for the dashboard progress bar (owner decision 2026-10-20).

A milestone is written only when its step has really been reached: source and values, the ordered gate, the signed-in session,
the duplicate checks, the verified form, and (only for a passing run) the recorded result. A refusal never marks a later step as
reached. The CE-only dry run writes none (unchanged). Names and outcomes only, never a value.
Hermetic: the progress file is a temporary one.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integration.onboarding import run_progress
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run as dry
import tools.attended_ce_only_playwright as runner

FULL = ["pdr_source", "pdr_gate", "pdr_signed_in", "pdr_duplicates", "pdr_form", "pdr_recorded"]


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "progress.json"
        patcher = mock.patch.object(runner, "PROGRESS_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def steps(self):
        return [event["step"] for event in run_progress.read_milestones(self.path, c3.CO)]


class MilestoneTests(Base):
    def test_a_passing_run_records_every_step_in_order(self):
        h = c3.C3Harness(self)
        self.assertEqual(h.go()["result"], "dry_run_passed")
        self.assertEqual(self.steps(), FULL)
        events = run_progress.read_milestones(self.path, c3.CO)
        self.assertTrue(all(event["outcome"] == "ok" and event["t"].endswith("Z") for event in events))
        view = run_progress.build_progress(run_progress.PRODUCTION_DRY_RUN_ROUTE, events)
        self.assertEqual([step.state for step in view.steps], ["done"] * 6)

    def test_a_refusal_never_marks_a_later_step_as_reached(self):
        cases = (("duplicate", dict(search={c3.MAIN: c3.hit(domain=c3.MAIN)}), ["pdr_source", "pdr_gate", "pdr_signed_in"]),
                 ("form", dict(max_scan="absent"), ["pdr_source", "pdr_gate", "pdr_signed_in", "pdr_duplicates"]))
        for label, kwargs, expected in cases:
            with self.subTest(refused_at=label):
                self.path.unlink(missing_ok=True)
                h = c3.C3Harness(self, **kwargs)
                self.assertEqual(h.go()["outcome"], "refused")
                self.assertEqual(self.steps(), expected)

    def test_a_refusal_before_the_browser_records_only_what_was_reached(self):
        for label, setup, expected in (("alternates", lambda h: setattr(h, "source", c3.make_case3_source(alternates=("a.example",))), []),
                                       ("source", lambda h: setattr(h, "source", runner.SurfaceSourceError("case3_core_plus_missing")), []),
                                       ("clone", None, ["pdr_source"])):
            with self.subTest(refused_at=label):
                self.path.unlink(missing_ok=True)
                h = c3.C3Harness(self)
                if setup:
                    setup(h)
                out = h.go(clone=(lambda *_a: {}) if label == "clone" else dry.good_clone)
                self.assertEqual(out["outcome"], "refused")
                self.assertEqual(self.steps(), expected)
                self.assertEqual(h.opened, 0)

    def test_the_ce_only_dry_run_writes_no_milestone(self):
        h = dry.Harness(self)
        self.assertEqual(h.run()["result"], "dry_run_passed")
        self.assertFalse(self.path.exists())

    def test_a_progress_file_that_cannot_be_written_never_breaks_the_run(self):
        with mock.patch.object(run_progress, "record_milestone", side_effect=OSError("disk full")):
            h = c3.C3Harness(self)
            self.assertEqual(h.go()["result"], "dry_run_passed")


if __name__ == "__main__":
    unittest.main()
