"""The desktop launcher's opt-in production sessions switch (2026-10-19, owner-authorized CO-0798 PC create).

The create gate needs ``SURFACE_ONBOARDING_PRODUCTION_SESSIONS=1`` in the dashboard process; the normal launcher never set it,
so every Create refused ``production_locked``. ``-ProductionSessions`` sets it; without the switch the launcher clears any
inherited value, so a Dev-only start can never open the production path by accident. Text checks only: nothing is run.
"""

import re
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "tools" / "start_attended_dashboard.ps1"


class LauncherSwitchTests(unittest.TestCase):
    def setUp(self):
        self.text = LAUNCHER.read_text(encoding="utf-8")

    def test_the_switch_is_declared_and_documented(self):
        self.assertRegex(self.text, r"\[switch\]\$ProductionSessions")
        self.assertIn("-ProductionSessions", self.text.split("#>")[0])

    def test_the_flag_is_set_only_with_the_switch_and_cleared_otherwise(self):
        block = re.search(r"if \(\$ProductionSessions\) \{(.*?)\} else \{(.*?)\}", self.text, flags=re.S)
        self.assertIsNotNone(block)
        self.assertIn("$env:SURFACE_ONBOARDING_PRODUCTION_SESSIONS = '1'", block.group(1))
        self.assertIn("Remove-Item Env:SURFACE_ONBOARDING_PRODUCTION_SESSIONS", block.group(2))

    def test_the_flag_is_settled_before_the_dashboard_process_starts(self):
        self.assertLess(self.text.index("if ($ProductionSessions)"), self.text.index("Start-Process -FilePath $PythonPath"))

    def test_the_launcher_sets_no_approval_values(self):
        # Approval reference / times come from the approval record inside the gate, never from the launcher.
        for name in ("SURFACE_ONBOARDING_PRODUCTION_APPROVAL", "SURFACE_ONBOARDING_PRODUCTION_SESSION_REF"):
            self.assertNotIn(name, self.text)


if __name__ == "__main__":
    unittest.main()
