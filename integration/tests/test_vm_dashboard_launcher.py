import os
from pathlib import Path
import stat
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

    @unittest.skipIf(os.name == "nt", "Windows does not preserve POSIX executable modes")
    def test_ubuntu_launcher_is_executable(self):
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_vm_dashboard.sh"
        self.assertTrue(script_path.stat().st_mode & stat.S_IXUSR)
