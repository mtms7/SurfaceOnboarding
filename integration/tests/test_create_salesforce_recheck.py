"""Fix 4 (2026-10-19, CO-0798 PC create): a Salesforce sign-in that was READY but is now older than the start TTL is
re-checked ONCE, read-only, when the create gate is evaluated, instead of refusing "sign in again" 15 minutes after a
good sign-in. Never signed in, signing in, expired or blocked are not probed. Hermetic: the probe is a fake.
"""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import session_readiness as readiness
from integration.onboarding.session_readiness import SessionState, SessionStatus

NOW = datetime(2026, 10, 19, 18, 0)


class SalesforceRecheckTests(unittest.TestCase):
    def ready(self, status, probe_result=None):
        probe = patch.object(dashboard, "probe_salesforce_readiness",
                             return_value=probe_result or SessionStatus(SessionState.READY, "salesforce_ready", NOW))
        with patch.object(dashboard, "session_statuses", return_value={"salesforce": status}), \
                patch.object(dashboard, "readiness_now", return_value=NOW), probe as probed:
            return dashboard._salesforce_ready_for_create(), probed.call_count

    def test_a_fresh_ready_session_is_ready_without_a_probe(self):
        self.assertEqual(self.ready(SessionStatus(SessionState.READY, "salesforce_ready", NOW - timedelta(minutes=1))), (True, 0))

    def test_a_ready_session_past_the_ttl_is_rechecked_once(self):
        old = SessionStatus(SessionState.READY, "salesforce_ready", NOW - readiness.START_SALESFORCE_TTL - timedelta(minutes=1))
        self.assertEqual(self.ready(old), (True, 1))
        expired = SessionStatus(SessionState.EXPIRED, "salesforce_sign_in_required", NOW)
        self.assertEqual(self.ready(old, probe_result=expired), (False, 1))

    def test_other_states_are_not_probed_and_not_ready(self):
        for state in (SessionState.NOT_SIGNED_IN, SessionState.SIGNING_IN, SessionState.EXPIRED, SessionState.BLOCKED):
            self.assertEqual(self.ready(SessionStatus(state, "x_reason", NOW - timedelta(hours=1))), (False, 0), state)

    def test_a_probe_failure_is_not_ready(self):
        old = SessionStatus(SessionState.READY, "salesforce_ready", NOW - timedelta(hours=1))
        with patch.object(dashboard, "session_statuses", return_value={"salesforce": old}), \
                patch.object(dashboard, "readiness_now", return_value=NOW), \
                patch.object(dashboard, "probe_salesforce_readiness", side_effect=RuntimeError("boom")):
            self.assertFalse(dashboard._salesforce_ready_for_create())

    def test_the_create_inputs_use_it(self):
        with patch.object(dashboard, "_salesforce_ready_for_create", return_value=True) as helper, \
                patch.object(dashboard, "_clone_status", return_value=None):
            inputs = dashboard._create_ui_inputs("CO-0798", None, operator="owner@example.test")
        self.assertTrue(inputs.sf_ready)
        helper.assert_called_once()


if __name__ == "__main__":
    unittest.main()
