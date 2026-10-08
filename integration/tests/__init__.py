"""Focused tests for the local integration scaffold.

Package-wide guard (2026-10-07): no test may write the operator's real local state under integration/
(a CE create scenario wrote attended_spycloud.json and the scan-status tests wrote the run log). Every state file
the runner and the dashboard write points to one temporary folder for the whole test run; a test that needs a
file still patches its own path. The runner is patched before the dashboard is imported, because the dashboard
copies some runner paths at import time.
"""

import atexit
import shutil
import sys
import tempfile
import types
from pathlib import Path

# CI (plain Python, 2026-10-07) has no Playwright package. The tests never start a real browser (they patch in
# fakes), but some patch "playwright.sync_api.sync_playwright" and the runner imports it before its pre-browser
# gates. Without the package those tests failed only on CI. A placeholder module whose sync_playwright refuses to
# run keeps the tests identical everywhere; where Playwright is installed nothing changes.
try:
    import playwright.sync_api  # noqa: F401
except ModuleNotFoundError:
    def _no_browser_in_tests(*_args, **_kwargs):
        raise RuntimeError("playwright_not_installed_in_tests")

    import importlib.machinery

    _package = types.ModuleType("playwright")
    _package.__spec__ = importlib.machinery.ModuleSpec("playwright", None)  # preflight uses importlib find_spec
    _sync_api = types.ModuleType("playwright.sync_api")
    _sync_api.__spec__ = importlib.machinery.ModuleSpec("playwright.sync_api", None)
    _sync_api.sync_playwright = _no_browser_in_tests
    _package.sync_api = _sync_api
    sys.modules.setdefault("playwright", _package)
    sys.modules.setdefault("playwright.sync_api", _sync_api)

_STATE_DIR = Path(tempfile.mkdtemp(prefix="surface_test_state_"))
atexit.register(shutil.rmtree, _STATE_DIR, True)

import tools.attended_ce_only_playwright as _runner  # noqa: E402

for _name in ("RUNNER_STATE_PATH", "CHECK_STATE_PATH", "READBACK_PATH", "SCAN_STATUS_PATH", "VALIDATION_PATH",
              "DIAGNOSTICS_PATH", "RUN_LOG_PATH", "PROGRESS_PATH", "SPYCLOUD_STATE_PATH", "RENEWAL_OUTCOMES_PATH", "MIRROR_PATH"):
    setattr(_runner, _name, _STATE_DIR / getattr(_runner, _name).name)

import tools.serve_attended_open_onboardings_dashboard as _dashboard  # noqa: E402

for _name in ("ATTENDED_LEONARDO_READBACK_PATH", "ATTENDED_REMINDERS_PATH", "USER_CREATED_CONFIRMATION_PATH",
              "ID_WRITEBACK_PATH"):
    setattr(_dashboard, _name, _STATE_DIR / getattr(_dashboard, _name).name)
