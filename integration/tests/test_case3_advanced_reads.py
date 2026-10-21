"""CO-0800 production dry run, step (d): learn-only reads of the Surface controls.

Owner answers 2026-10-20: Maximum scan Duration is 90 h in production; the dry run reads the control (its default), sets 90, reads it
back, Cancels (nothing submitted). The nine Advanced switches are read and compared (already done for CE-only) and whether the
"Scan now" control exists once a Weekly/Monthly interval is chosen is observed. Findings are names and short tokens only (a new
``observations`` record field). A missing, disabled or sticky Max-duration control refuses the dry run with a specific code (that
refusal IS the finding). The CE-only dry run is byte-for-byte unchanged: no new field, no new record key.
Hermetic: fake page, fake source, temporary folders.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_dry_run as pdr
from integration.tests import test_case3_dry_run_runner as c3
from integration.tests import test_production_dry_run as dry
import tools.attended_ce_only_playwright as runner

LABEL = "Maximum scan Duration (hours)"
KEY = "advanced_text.maximum_scan_duration_hours"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


C3Page, Harness = c3.C3Page, c3.C3Harness


class MappingTests(unittest.TestCase):
    def test_the_case_3_plan_carries_the_duration_and_the_ce_plan_does_not(self):
        plan = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
        intended = pdr.map_plan(plan, runner.SELECT_NAMES, runner.EARLY_SELECTS)
        self.assertEqual(intended.advanced_texts, ((LABEL, "90"),))
        self.assertIn(KEY, pdr.field_keys(intended))
        ce = pdr.map_plan(runner.build_ce_only_fill(dry.make_source()), runner.SELECT_NAMES, runner.EARLY_SELECTS)
        self.assertEqual(ce.advanced_texts, ())
        self.assertNotIn(KEY, pdr.field_keys(ce))

    def test_only_the_known_label_and_a_plain_number_are_mapped(self):
        base = runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY)
        for texts, reason in (({"Some other control": "90"}, "unmapped_text_label"), ({LABEL: "9x"}, "fill_value_invalid"),
                              ({LABEL: ""}, "fill_value_invalid"), ({LABEL: "0"}, "fill_value_invalid"),
                              ({LABEL: "1000"}, "fill_value_invalid"), ({LABEL: 90}, "fill_value_invalid")):
            with self.subTest(texts=texts), self.assertRaises(pdr.MappingError) as caught:
                pdr.map_plan({**base, "advanced_texts": texts}, runner.SELECT_NAMES, runner.EARLY_SELECTS)
            self.assertEqual(caught.exception.args[0], reason)
            self.assertEqual(pdr.known_reason(reason), reason)

    def test_the_comparison_passes_only_for_the_exact_value(self):
        intended = pdr.map_plan(runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY), runner.SELECT_NAMES,
                                runner.EARLY_SELECTS)
        for value, want in (("90", "pass"), ("24", "fail"), (None, "fail"), (" 90", "fail")):
            with self.subTest(value=value):
                self.assertEqual(pdr.compare_reads(intended, {"advanced_texts": {LABEL: value}})[KEY], want)
        self.assertEqual(pdr.compare_reads(intended, {})[KEY], "fail")  # not read is never a pass


class RecordTests(unittest.TestCase):
    def record(self, **extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = pcr.record(tmp, {"co_reference": "CO-0800", "outcome": "refused", "refusal_reason": "advanced_mismatch", **extra},
                              NOW)
            return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_observations_keep_names_and_short_tokens_only(self):
        saved = self.record(observations={"max_scan_duration_default": "24", "scan_now_control": "absent",
                                          "bad name!": "x", "free_text": "Customer Name GmbH", "token": "abcdefghij" * 5,
                                          "long": "a" * 30})
        self.assertEqual(saved["observations"], {"max_scan_duration_default": "24", "scan_now_control": "absent"})

    def test_a_record_without_observations_is_unchanged(self):
        self.assertNotIn("observations", self.record())
        self.assertNotIn("observations", self.record(observations="not a dict"))


class RunTests(unittest.TestCase):
    def test_a_case_3_dry_run_sets_90_reads_it_back_and_records_the_findings(self):
        h = Harness(self, scan_now_hides=True)
        out = h.go()
        self.assertEqual(out["result"], "dry_run_passed")
        record, text = h.record()
        self.assertEqual(h.page.texts[LABEL].value, "90")
        self.assertEqual(record["form_fields_checked"][KEY], "pass")
        self.assertEqual(record["observations"], {"scan_now_control": "absent", "max_scan_duration_control": "present",
                                                  "max_scan_duration_default": "24"})
        self.assertNotIn("Confirm", h.page.clicks)
        self.assertIn("Cancel", h.page.clicks)

    def test_scan_now_still_present_under_a_schedule_is_recorded_not_judged(self):
        h = Harness(self, scan_now_hides=False)
        self.assertEqual(h.go()["result"], "dry_run_passed")
        self.assertEqual(h.record()[0]["observations"]["scan_now_control"], "present")

    def test_a_missing_disabled_or_sticky_duration_control_refuses_and_still_cancels(self):
        for variant, reason in (("absent", "max_scan_duration_control_absent"), ("disabled", "max_scan_duration_control_absent"),
                                ("stuck", "fill_value_mismatch")):
            with self.subTest(variant=variant):
                h = Harness(self, max_scan=variant)
                out = h.go()
                self.assertEqual((out["result"], out["outcome"]), (reason, "refused"))
                self.assertNotIn("Confirm", h.page.clicks)
                self.assertFalse(h.page.form_open)
                record = h.record()[0]
                if variant != "stuck":
                    self.assertEqual(record["observations"]["max_scan_duration_control"], variant)
                self.assertIsNone(h.case3_approval_unused())

    def test_the_ce_only_dry_run_is_unchanged(self):
        h = dry.Harness(self)
        out = h.run()
        self.assertEqual(out["result"], "dry_run_passed")
        record = h.record()[0]
        self.assertNotIn("observations", record)
        self.assertFalse([key for key in record["form_fields_checked"] if key.startswith("advanced_text.")])
        self.assertNotIn(LABEL, h.page.texts)

    def test_the_form_reads_include_the_duration_only_for_a_surface_plan(self):
        page = C3Page()
        page.form_open = True
        surface = pdr.map_plan(runner.build_case3_fill(c3.make_case3_source(), c3.RUN_DAY), runner.SELECT_NAMES,
                               runner.EARLY_SELECTS)
        ce = pdr.map_plan(runner.build_ce_only_fill(dry.make_source()), runner.SELECT_NAMES, runner.EARLY_SELECTS)
        self.assertEqual(runner._pdr_form_reads(page, surface, lambda *_a: True)["advanced_texts"], {LABEL: "24"})
        self.assertNotIn("advanced_texts", runner._pdr_form_reads(page, ce, lambda *_a: True))


if __name__ == "__main__":
    unittest.main()
