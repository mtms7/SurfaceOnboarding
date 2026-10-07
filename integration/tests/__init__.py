"""Focused tests for the local integration scaffold.

Package-wide guard (2026-10-07): no test may write the operator's real local state under integration/
(a CE create scenario wrote attended_spycloud.json and the scan-status tests wrote the run log). Every state file
the runner and the dashboard write points to one temporary folder for the whole test run; a test that needs a
file still patches its own path. The runner is patched before the dashboard is imported, because the dashboard
copies some runner paths at import time.
"""

import atexit
import shutil
import tempfile
from pathlib import Path

_STATE_DIR = Path(tempfile.mkdtemp(prefix="surface_test_state_"))
atexit.register(shutil.rmtree, _STATE_DIR, True)

import tools.attended_ce_only_playwright as _runner  # noqa: E402

for _name in ("RUNNER_STATE_PATH", "CHECK_STATE_PATH", "READBACK_PATH", "SCAN_STATUS_PATH", "VALIDATION_PATH",
              "DIAGNOSTICS_PATH", "RUN_LOG_PATH", "SPYCLOUD_STATE_PATH", "RENEWAL_OUTCOMES_PATH", "MIRROR_PATH"):
    setattr(_runner, _name, _STATE_DIR / getattr(_runner, _name).name)

import tools.serve_attended_open_onboardings_dashboard as _dashboard  # noqa: E402

for _name in ("ATTENDED_LEONARDO_READBACK_PATH", "ATTENDED_REMINDERS_PATH", "USER_CREATED_CONFIRMATION_PATH",
              "ID_WRITEBACK_PATH"):
    setattr(_dashboard, _name, _STATE_DIR / getattr(_dashboard, _name).name)
