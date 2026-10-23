"""Unlock window 2026-10-22 (owner go): ``PRODUCTION_RUNNER_SUPPORTED`` is True in this build for ONE attended CO-0802 (Case 3)
create. Tests written for the locked build run with the create lock patched back to False through ``locked_create_build`` so they
keep proving the locked paths; ``test_unlock_state`` pins the real build state. The relock commit reverts this file's users and
deletes it."""
from __future__ import annotations

from unittest import mock

locked_create_build = mock.patch("integration.onboarding.session_readiness.PRODUCTION_RUNNER_SUPPORTED", False)
