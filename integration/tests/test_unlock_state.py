"""The REAL lock state of this build: RELEASED 2026-10-23 (owner decision, docs/61 option A) for the Case 3 create and dry run under
per-CO approvals. A relock is a git revert of the unlock commits and flips these back."""
from __future__ import annotations

from pathlib import Path
import re
import unittest

from integration.onboarding import session_readiness


class UnlockStateTests(unittest.TestCase):
    def test_both_production_constants_are_unlocked_in_this_build_only_by_the_reviewed_lines(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, True)
        self.assertIs(session_readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)
        text = Path(session_readiness.__file__).read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^PRODUCTION_RUNNER_SUPPORTED = True  # RELEASED 2026-10-23 \(owner decision, docs/61\)")
        self.assertRegex(text, r"(?m)^PRODUCTION_DRY_RUN_SUPPORTED = True  # RELEASED 2026-10-23 \(owner decision, docs/61\)")
        self.assertEqual(len(re.findall(r"(?m)^PRODUCTION_RUNNER_SUPPORTED = ", text)), 1)
        self.assertEqual(len(re.findall(r"(?m)^PRODUCTION_DRY_RUN_SUPPORTED = ", text)), 1)


if __name__ == "__main__":
    unittest.main()
