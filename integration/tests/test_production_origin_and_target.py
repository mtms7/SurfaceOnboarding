from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import unittest
from unittest import mock

from integration.onboarding import session_readiness
from integration.onboarding.config import AppConfig, PollSchedule
from integration.onboarding.origin_policy import (LEONARDO_DEVELOPMENT_ORIGIN, PRODUCTION_BACKOFFICE_ORIGIN,
                                                  require_development_origin, require_production_origin)
from integration.onboarding.run_target import ProductionLocked, RunTarget, development_target, production_target
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
P = "SURFACE_ONBOARDING_PRODUCTION_"


def good_environ() -> dict[str, str]:
    return {
        P + "SESSIONS": "1",
        P + "APPROVAL_REF": "APPROVAL-2026-10-16",
        P + "APPROVAL_ISSUED": (NOW - timedelta(days=1)).isoformat(),
        P + "APPROVAL_EXPIRES": (NOW + timedelta(days=5)).isoformat(),
    }


class ProductionOriginTests(unittest.TestCase):
    def test_exact_production_origin_is_accepted(self):
        self.assertEqual(require_production_origin("https://app.pentera.io"), "https://app.pentera.io")
        self.assertEqual(require_production_origin(PRODUCTION_BACKOFFICE_ORIGIN), PRODUCTION_BACKOFFICE_ORIGIN)

    def test_every_variation_is_rejected(self):
        for origin in (
            "http://app.pentera.io", "https://app.pentera.io:443", "https://app.pentera.io:8443",
            "https://www.app.pentera.io", "https://leonardo.dev.app.pentera.io",
            "https://app.pentera.io@evil.example", "https://evil.example@app.pentera.io",
            "https://app.pentera.io/", "https://app.pentera.io/login",
            "https://app.pentera.io?next=x", "https://app.pentera.io#frag",
            "https://APP.PENTERA.IO", "https://app.pentera.io ", " https://app.pentera.io",
            "https://app.pentera.io\n", "", "https://app.pentera.io.evil.example",
            "https://evilapp.pentera.io", "https://app.pentera.io.",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                require_production_origin(origin)

    def test_non_str_is_rejected(self):
        for value in (None, 1, b"https://app.pentera.io", ["https://app.pentera.io"]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "invalid"):
                require_production_origin(value)  # type: ignore[arg-type]

    def test_origins_do_not_cross_over(self):
        with self.assertRaises(ValueError):
            require_production_origin(LEONARDO_DEVELOPMENT_ORIGIN)
        with self.assertRaises(ValueError):
            require_development_origin(PRODUCTION_BACKOFFICE_ORIGIN)


class RunTargetTests(unittest.TestCase):
    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_runner_support_flag_stays_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    def test_development_target_is_the_default(self):
        self.assertEqual(development_target(),
                         RunTarget("leonardo_development", LEONARDO_DEVELOPMENT_ORIGIN, "leonardo-automation"))

    def test_profile_dirs_differ(self):
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            prod = production_target(good_environ(), NOW)
        self.assertNotEqual(prod.profile_dir_name, development_target().profile_dir_name)

    def _locked(self, environ, reason):
        with self.assertRaises(ProductionLocked) as caught:
            production_target(environ, NOW)
        self.assertEqual(caught.exception.reason, reason)
        self.assertIn(reason, str(caught.exception))

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_each_unlock_problem_locks_in_order(self):
        env = good_environ()
        self._locked({}, "production_sessions_flag_missing")
        self._locked({**env, P + "SESSIONS": "0"}, "production_sessions_flag_missing")
        self._locked({**env, P + "APPROVAL_REF": "bad"}, "production_approval_missing")
        self._locked({**env, P + "APPROVAL_ISSUED": "nope"}, "production_approval_missing")
        self._locked({**env, P + "APPROVAL_EXPIRES": "2026-10-20T00:00:00"}, "production_approval_missing")
        self._locked({**env, P + "APPROVAL_EXPIRES": (NOW - timedelta(hours=1)).isoformat()},
                     "production_approval_expired")
        self._locked({**env, P + "APPROVAL_EXPIRES": (NOW + timedelta(days=40)).isoformat()},
                     "production_approval_expired")
        # Everything valid, but the runner lock still holds.
        self._locked(env, "production_runner_not_supported")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_valid_environ_succeeds_only_with_runner_support_patched(self):
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            target = production_target(good_environ(), NOW)
        self.assertEqual(target, RunTarget("backoffice_production", PRODUCTION_BACKOFFICE_ORIGIN,
                                           "backoffice-production-automation"))
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    def test_app_config_still_rejects_production(self):
        schedule = PollSchedule("UTC", (time(8), time(16)))
        with self.assertRaisesRegex(ValueError, "production"):
            AppConfig("production", schedule)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
