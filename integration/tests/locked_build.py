"""Release 2026-10-23 (owner decision, docs/61 option A): ``PRODUCTION_RUNNER_SUPPORTED`` is True in this build (Case 3 create under
per-CO approvals). Tests written for the locked build run with the create lock patched back to False through ``locked_create_build`` so they
keep proving the locked paths; ``test_unlock_state`` pins the real build state. The relock commit reverts this file's users and
deletes it."""
from __future__ import annotations

from unittest import mock

locked_create_build = mock.patch("integration.onboarding.session_readiness.PRODUCTION_RUNNER_SUPPORTED", False)
