"""CO-0844 (2026-11-11): "Move the stage to User Created" waited ~30-40 s on Salesforce inside the click and looked like
nothing happened. The move now runs in the background: the click returns at once, the card says "Moving the stage..." and
the page refreshes until it ends; a second click meanwhile is refused (never two moves)."""
from __future__ import annotations

import threading
import unittest
from unittest import mock

import tools.serve_attended_open_onboardings_dashboard as dashboard

CO = "CO-0901"


class StageMoveAsyncTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(dashboard.PRIMARY_USER_RUNS.pop, CO, None)
        self.release = threading.Event()
        self.moves = []

        def slow_move(reference):
            self.moves.append(reference)
            self.release.wait(5)
            return "written_verified"

        for patch in (mock.patch.object(dashboard, "_primary_user_stage_move", side_effect=slow_move),
                      mock.patch.object(dashboard, "clear_display_cache_for")):
            patch.start()
            self.addCleanup(patch.stop)

    def wait_done(self):
        for _ in range(100):
            with dashboard._PRODUCTION_RUNS_LOCK:  # noqa: SLF001
                if not dashboard.PRIMARY_USER_RUNS[CO].get("stage_moving"):
                    return
            threading.Event().wait(0.02)
        self.fail("stage move did not finish")

    def test_the_click_returns_at_once_and_the_page_refreshes_while_moving(self):
        dashboard._start_primary_user_stage_move(CO)  # noqa: SLF001
        self.assertTrue(dashboard.PRIMARY_USER_RUNS[CO]["stage_moving"])
        with mock.patch.object(dashboard, "primary_user_run_state", return_value=None):
            self.assertIn("http-equiv='refresh'", dashboard._primary_user_refresh(CO))  # noqa: SLF001
        self.release.set()
        self.wait_done()
        self.assertEqual(dashboard.PRIMARY_USER_RUNS[CO]["stage"], "written_verified")
        with mock.patch.object(dashboard, "primary_user_run_state", return_value=None):
            self.assertEqual(dashboard._primary_user_refresh(CO), "")  # noqa: SLF001
        self.assertEqual(self.moves, [CO])

    def test_a_second_click_while_moving_is_refused(self):
        dashboard._start_primary_user_stage_move(CO)  # noqa: SLF001
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(dashboard.create_ui, "load_co_records",
                                  return_value=[{"outcome": "readback_verified", "route": dashboard.CASE3_ENGINE}]):
            self.assertEqual(dashboard.primary_user_gate(CO, {}), "primary_user_running")
        self.release.set()
        self.wait_done()

    def test_a_failed_move_is_a_word_not_a_crash(self):
        with mock.patch.object(dashboard, "_primary_user_stage_move", side_effect=RuntimeError("boom")):
            dashboard._start_primary_user_stage_move(CO)  # noqa: SLF001
            self.wait_done()
        self.assertEqual(dashboard.PRIMARY_USER_RUNS[CO]["stage"], "stage_move_failed")


if __name__ == "__main__":
    unittest.main()
