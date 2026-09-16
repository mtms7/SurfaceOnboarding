from pathlib import Path
import unittest


class VmDashboardLauncherTests(unittest.TestCase):
    def test_ubuntu_launcher_uses_the_managed_python312_runtime_and_fails_closed(self):
        script = (Path(__file__).resolve().parents[2] / "scripts" / "run_vm_dashboard.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "/opt/surface-onboarding/runtime/python-3.12.14/bin/python3.12",
            script,
        )
        self.assertIn("Python 3.12 runtime is unavailable", script)
        self.assertIn("must be Python 3.12 or newer", script)
        self.assertNotIn('SURFACE_ONBOARDING_PYTHON:-python3', script)
