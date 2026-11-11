"""Case 3 primary user: progress bar, final banner, newest result and single-flight launch (owner request 2026-11-11, CO-0844)."""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import case3_followup
from integration.onboarding import primary_user_progress as pu
from integration.onboarding import production_case3_primary_user as primary
from integration.onboarding import production_create_ui as create_ui
from integration.onboarding import run_progress

NOW = datetime(2026, 11, 11, 22, 0, tzinfo=timezone.utc)
STAMP = "%Y-%m-%dT%H:%M:%SZ"
VERIFIED = {"outcome": "readback_verified", "route": "case_3_combined_baseline", "co_reference": "CO-0844",
            "recorded_at": "2026-11-10T20:44:44Z", "account_id": "a" * 24, "tenant_name": "Example Tenant"}


def co_row(**changes):
    row = {"Id": "a0B000000000001AAA", "Name": "CO-0844", "Onboarding_Product__c": "Surface & Credential Exposure",
           "Onboarding_Type__c": "New Product Onboarding", "Onboarding_Stage__c": "Scan Completed Successfully",
           "Main_Domain__c": "example.org", "Email_Domains__c": "example.org", "Primary_User__c": "003000000000001",
           "Primary_User__r": {"FirstName": "Ada", "LastName": "Lovelace", "Email": "ada.lovelace@example.org"}}
    row.update(changes)
    return row


def ev(*pairs, start=NOW):
    out = [{"t": start.strftime(STAMP), "step": "pu_start", "outcome": "ok"}]
    for index, (step, outcome) in enumerate(pairs, 1):
        out.append({"t": (start + timedelta(seconds=index)).strftime(STAMP), "step": step, "outcome": outcome})
    return out


def states(view):
    return [state for _label, state in view["steps"]]


class StepMappingTests(unittest.TestCase):
    def test_labels_are_the_owner_steps(self):
        self.assertEqual(len(pu.STEPS), 7)
        self.assertTrue(pu.STEPS[1].endswith("(Access)"))
        self.assertIn("User Created", pu.STEPS[6])

    def test_running_steps_advance_with_milestones_only(self):
        view = pu.progress(ev(), running=True)
        self.assertEqual(states(view), ["active"] + ["pending"] * 6)
        self.assertEqual(view["status"], "running")
        self.assertLessEqual(view["percent"], 95)
        view = pu.progress(ev(("pu_signed_in", "ok"), ("pu_tenant_found", "ok"), ("pu_control_taken", "ok")), running=True)
        self.assertEqual(states(view), ["done", "done", "active", "pending", "pending", "pending", "pending"])
        self.assertEqual(view["current"], pu.STEPS[2])

    def test_created_and_verified_then_stage(self):
        events = ev(("pu_signed_in", "ok"), ("pu_tenant_found", "ok"), ("pu_control_taken", "ok"), ("pu_users_read", "absent"),
                    ("pu_add_sent", "add_accepted"), ("pu_add_verified", "present_admin"), ("pu_control_released", "ok"),
                    ("pu_result", primary.CREATED))
        waiting = pu.progress(events, running=False, follow_running=True)
        self.assertEqual(states(waiting), ["done"] * 6 + ["active"])
        self.assertEqual(waiting["status"], "running")
        done = pu.progress(events, running=False, stage="written_verified")
        self.assertEqual(states(done), ["done"] * 7)
        self.assertEqual((done["percent"], done["status"]), (100, "done"))
        failed_stage = pu.progress(events, running=False, stage="stage_not_expected")
        self.assertEqual(states(failed_stage)[-1], "failed")

    def test_already_present_skips_the_add_and_readback(self):
        events = ev(("pu_tenant_found", "ok"), ("pu_control_taken", "ok"), ("pu_users_read", "present_admin"),
                    ("pu_control_released", "ok"), ("pu_result", primary.ALREADY))
        view = pu.progress(events, running=False, stage="already_present")
        self.assertEqual(states(view), ["done", "done", "done", "skipped", "skipped", "done", "done"])
        self.assertEqual(view["percent"], 100)

    def test_control_item_not_found_fails_step_two_and_skips_the_rest(self):
        events = ev(("pu_signed_in", "ok"), ("pu_tenant_found", "ok"), ("pu_result", "control_item_not_found"))
        view = pu.progress(events, running=False, stage="primary_user_not_done")
        self.assertEqual(states(view), ["done", "failed", "skipped", "skipped", "skipped", "skipped", "skipped"])
        self.assertEqual(view["status"], "failed")
        self.assertEqual(view["current"], pu.STEPS[1])

    def test_add_form_unavailable_and_control_not_released(self):
        events = ev(("pu_tenant_found", "ok"), ("pu_control_taken", "ok"), ("pu_users_read", "absent"),
                    ("pu_add_sent", "add_form_unavailable"), ("pu_control_released", "failed"),
                    ("pu_result", "add_form_unavailable+control_not_released"))
        view = pu.progress(events, running=False)
        self.assertEqual(states(view), ["done", "done", "done", "failed", "skipped", "failed", "skipped"])
        self.assertEqual(view["result"], "add_form_unavailable+control_not_released")

    def test_sign_in_timeout_fails_the_first_step(self):
        view = pu.progress(ev(("pu_result", "production_sign_in_timeout")), running=False)
        self.assertEqual(states(view)[0], "failed")
        self.assertEqual(states(view)[1:], ["skipped"] * 6)

    def test_a_runner_that_ended_without_a_result_is_not_shown_as_running(self):
        view = pu.progress(ev(("pu_tenant_found", "ok"), ("pu_control_taken", "ok")), running=False)
        self.assertEqual(view["result"], "runner_ended_without_a_result")
        self.assertEqual(states(view)[2], "failed")
        self.assertEqual(states(view)[5], "failed")  # control was taken and never given back on record

    def test_only_the_newest_run_counts(self):
        old = ev(("pu_result", "control_item_not_found"), start=NOW - timedelta(minutes=20))
        new = ev(("pu_tenant_found", "ok"), start=NOW)
        self.assertEqual(pu.run_result(old + new), None)
        self.assertEqual(pu.current_run(old + new)[0]["t"], NOW.strftime(STAMP))
        self.assertEqual(pu.current_run([{"t": "x", "step": "pdr_source", "outcome": "ok"}]), [])


class BannerTests(unittest.TestCase):
    def test_every_result_word_has_a_clear_verdict(self):
        ok = {primary.CREATED: "Customer Admin user created and verified", primary.ALREADY: "Customer Admin user already present",
              primary.BY_HAND: "Confirmed by hand"}
        for word, head in ok.items():
            (css, text), = pu.banner(word)
            self.assertEqual(css, "ok")
            self.assertTrue(text.startswith(head), word)
        others = [w for w in primary.RESULT_TEXT if w not in primary.DONE_RESULTS] + [
            "production_sign_in_timeout", "production_window_signed_out", "primary_user_failed", "no_verified_case3_create",
            "primary_user_needs_review", "scan_not_marked_finished", "runner_ended_without_a_result", "records_unreadable"]
        for word in others:
            (css, text), = pu.banner(word)
            self.assertEqual(css, "bad", word)
            self.assertTrue(text.startswith("User NOT created: ") or text.startswith("User creation NOT confirmed: "), word)
            if word in primary.RESULT_TEXT:
                self.assertIn(primary.RESULT_TEXT[word], text)
        self.assertTrue(pu.banner("user_created_unverified")[0][1].startswith("User creation NOT confirmed"))
        self.assertEqual(pu.banner(None), [])

    def test_control_not_released_adds_the_amber_warning(self):
        lines = pu.banner("add_form_unavailable+" + primary.CONTROL_NOT_RELEASED)
        self.assertEqual([css for css, _t in lines], ["bad", "warn"])
        self.assertIn("Give up control", lines[1][1])
        self.assertIn("NOT given back", lines[1][1])
        created = pu.banner(primary.CREATED + "+" + primary.CONTROL_NOT_RELEASED)
        self.assertEqual([css for css, _t in created], ["ok", "warn"])

    def test_stage_line(self):
        self.assertEqual(pu.banner(primary.CREATED, "written_verified")[-1][0], "ok")
        self.assertIn("User Created", pu.banner(primary.CREATED, "written_verified")[-1][1])
        css, text = pu.banner("add_rejected", "primary_user_not_done")[-1]
        self.assertEqual(css, "bad")
        self.assertIn("NOT moved", text)

    def test_escaping_and_unsafe_words(self):
        html = pu.banner_html("<script>alert(1)</script>", "<b>x</b>")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        view = pu.progress(ev(("pu_result", "<img src=x>")), running=False)
        page = pu.card_html(view)
        self.assertNotIn("<img", page)
        self.assertIn("banner-bad", page)
        self.assertNotIn("<script", page)

    def test_card_shows_the_banner_only_once_finished(self):
        running = pu.card_html(pu.progress(ev(("pu_tenant_found", "ok")), running=True))
        self.assertIn("role='progressbar'", running)
        self.assertNotIn("banner", running)
        self.assertIn("(running)", running)
        finished = pu.card_html(pu.progress(ev(("pu_result", "control_failed")), running=False), "primary_user_not_done")
        self.assertIn("User NOT created", finished)
        self.assertIn("Salesforce stage NOT moved", finished)


class RunnerMilestoneTests(unittest.TestCase):
    def setUp(self):
        import tools.attended_ce_only_playwright as runner

        self.runner = runner
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.progress = self.parent / "progress.json"
        for patcher in (mock.patch.object(runner, "_launch_os_name", return_value="nt"),
                        mock.patch.object(runner, "PROGRESS_PATH", self.progress),
                        mock.patch("integration.onboarding.production_create_ui.load_co_records", return_value=[VERIFIED]),
                        mock.patch("integration.onboarding.production_create_ui.co_status",
                                   return_value={"code": create_ui.CREATED_VERIFIED})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _events(self):
        return run_progress.read_milestones(self.progress, "CO-0844")

    def _run(self, opener=None, row=None, now=NOW):
        line = self.runner.run_production_primary_user(
            "CO-0844", environ={"SURFACE_ONBOARDING_RUNTIME": "desktop"}, now=now, records_parent=self.parent,
            open_page=opener or mock.Mock(side_effect=AssertionError("no browser")), read_co=lambda q: [row or co_row()])
        return json.loads(line)["result"]

    def _page(self):
        page = mock.MagicMock()
        page.locator.return_value.evaluate_all.return_value = ["Button-Grid_Access", "Button-Grid_Delete"]
        page.locator.return_value.count.return_value = 1
        page.expect_response.return_value.__enter__.return_value.value.status = 200

        @contextmanager
        def opener(_target, _wait):
            yield page

        return opener

    def _flow(self, users, added="add_accepted", released=True):
        reader = mock.Mock()
        reader.readback.return_value = {"status": 200, "rows": [{"id": "a" * 24}]}
        with mock.patch.object(self.runner, "_pdr_reader", return_value=reader), \
                mock.patch.object(self.runner, "_pu_users_state", side_effect=users), \
                mock.patch.object(self.runner, "_pu_fill_and_add", return_value=added), \
                mock.patch.object(self.runner, "_pu_release", return_value=released):
            return self._run(self._page())

    def test_created_run_records_every_step_in_order(self):
        self.assertEqual(self._flow(["absent", "present_admin"]), primary.CREATED)
        steps = [(e["step"], e["outcome"]) for e in self._events()]
        self.assertEqual(steps, [("pu_start", "ok"), ("pu_signed_in", "ok"), ("pu_tenant_found", "ok"),
                                 ("pu_control_taken", "ok"), ("pu_users_read", "absent"), ("pu_add_sent", "add_accepted"),
                                 ("pu_add_verified", "present_admin"), ("pu_control_released", "ok"),
                                 ("pu_result", primary.CREATED)])
        view = pu.progress(self._events(), running=False, stage="written_verified")
        self.assertEqual(view["percent"], 100)
        self.assertEqual(case3_followup.last_primary_user(self.parent, "CO-0844"), primary.CREATED)

    def test_control_not_released_is_the_final_word(self):
        self.assertEqual(self._flow(["absent"], added="add_form_unavailable", released=False),
                         "add_form_unavailable+control_not_released")
        self.assertEqual(pu.run_result(self._events()), "add_form_unavailable+control_not_released")
        self.assertEqual([(css) for css, _t in pu.banner(pu.run_result(self._events()))], ["bad", "warn"])

    def test_gate_refusal_still_gives_a_final_result(self):
        self.assertEqual(self._run(row=co_row(Onboarding_Stage__c="Account Scanning")), "scan_not_marked_finished")
        self.assertEqual([e["step"] for e in self._events()], ["pu_start", "pu_result"])
        self.assertEqual(pu.run_result(self._events()), "scan_not_marked_finished")

    def test_milestones_hold_no_names_or_emails(self):
        self._flow(["absent", "present_admin"])
        text = self.progress.read_text(encoding="utf-8")
        for secret in ("Ada", "Lovelace", "ada.lovelace", "example.org", "@", "Example Tenant", "a" * 24):
            self.assertNotIn(secret, text)
        self.runner._pu_note("CO-0844", "pu_users_read", "ada.lovelace@example.org", lambda: NOW)  # noqa: SLF001
        self.runner._pu_note("CO-0844", "pu_users_read", "Ada Lovelace", lambda: NOW)  # noqa: SLF001
        text = self.progress.read_text(encoding="utf-8")
        self.assertNotIn("ada", text.lower().replace("pu_", ""))
        self.assertEqual([e["outcome"] for e in self._events()][-2:], ["other", "other"])

    def test_a_running_run_blocks_a_second_one_without_a_browser(self):
        run_progress.record_milestone(self.progress, "CO-0844", {"t": (NOW - timedelta(minutes=2)).strftime(STAMP),
                                                                 "step": "pu_start", "outcome": "ok"})
        self.assertEqual(self._run(), "primary_user_already_running")
        self.assertEqual([e["step"] for e in self._events()], ["pu_start"])  # the running run's events stay untouched
        # A start without a result older than STALE_SECONDS no longer blocks (a crashed run).
        later = NOW + timedelta(seconds=pu.STALE_SECONDS + 60)
        self.assertEqual(self._run(row=co_row(Onboarding_Stage__c="Account Scanning"), now=later), "scan_not_marked_finished")


class DashboardTests(unittest.TestCase):
    def setUp(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard

        self.d = dashboard
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = Path(self.tmp.name)
        self.progress = self.parent / "progress.json"
        runs: dict = {}
        for patcher in (mock.patch.object(dashboard.attended_runner, "PROGRESS_PATH", self.progress),
                        mock.patch.object(dashboard, "PRIMARY_USER_RUNS", runs),
                        mock.patch.object(dashboard, "production_records_parent", return_value=self.parent),
                        mock.patch.object(dashboard, "vm_mode", return_value=False),
                        mock.patch.object(dashboard.create_ui, "load_co_records", return_value=[VERIFIED]),
                        mock.patch.object(dashboard.create_ui, "LAUNCHES", create_ui.LaunchRegistry()),
                        mock.patch.object(dashboard, "_read_primary_user_row", return_value=co_row())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.runs = runs

    def _note(self, step, outcome="ok", at=None):
        run_progress.record_milestone(self.progress, "CO-0844", {"t": (at or datetime.now(timezone.utc)).strftime(STAMP),
                                                                 "step": step, "outcome": outcome})

    def _process(self, alive=True):
        process = mock.Mock()
        process.poll.return_value = None if alive else 0
        return process

    def test_refresh_only_while_running(self):
        self.assertEqual(self.d._primary_user_refresh("CO-0844"), "")  # noqa: SLF001
        since = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime(STAMP)
        process = self._process()
        self.runs["CO-0844"] = {"running": True, "stage": None, "since": since, "process": process}
        self.assertIn("refresh", self.d._primary_user_refresh("CO-0844"))  # noqa: SLF001
        process.poll.return_value = 0  # the runner ended; the stage move is still running
        self.assertIn("refresh", self.d._primary_user_refresh("CO-0844"))  # noqa: SLF001
        self.runs["CO-0844"] = {"running": False, "stage": "primary_user_not_done", "since": since, "process": process}
        self.assertEqual(self.d._primary_user_refresh("CO-0844"), "")  # noqa: SLF001

    def test_a_run_from_before_a_restart_still_counts_as_running(self):
        self._note("pu_start", at=datetime.now(timezone.utc) - timedelta(minutes=1))
        state = self.d.primary_user_run_state("CO-0844")
        self.assertTrue(state["active"])
        self.assertEqual(self.d.primary_user_gate("CO-0844", co_row()), "primary_user_running")
        self._note("pu_result", "control_failed")
        self.assertFalse(self.d.primary_user_run_state("CO-0844")["active"])
        self.assertIsNone(self.d.primary_user_gate("CO-0844", co_row()))

    def test_the_newest_result_is_shown_not_an_older_one(self):
        case3_followup.record_primary_user(self.parent, "CO-0844", "control_item_not_found",
                                           datetime.now(timezone.utc) - timedelta(minutes=20), "example.org")
        old = datetime.now(timezone.utc) - timedelta(minutes=20)
        self._note("pu_start", at=old)
        self._note("pu_result", "control_item_not_found", at=old)
        since = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(STAMP)
        self.runs["CO-0844"] = {"running": True, "stage": None, "since": since, "process": self._process()}
        state = self.d.primary_user_run_state("CO-0844")
        self.assertEqual(state["events"], [])  # the older run's milestones never count for this launch
        html = self.d.primary_user_result_html(state, "control_item_not_found", None, self.parent, "CO-0844")
        self.assertNotIn("take-control", html)
        self.assertNotIn("banner", html)
        self._note("pu_start")
        self._note("pu_result", "add_form_unavailable+control_not_released")
        self.runs["CO-0844"] = {"running": False, "stage": "primary_user_not_done", "since": since,
                                "process": self._process(alive=False)}
        state = self.d.primary_user_run_state("CO-0844")
        html = self.d.primary_user_result_html(state, "control_item_not_found", "primary_user_not_done", self.parent,
                                               "CO-0844")
        self.assertIn(primary.RESULT_TEXT["add_form_unavailable"], html)
        self.assertNotIn(primary.RESULT_TEXT["control_item_not_found"], html)
        self.assertIn("banner-warn", html)
        self.assertIn("Give up control", html)

    def test_recorded_result_of_this_run_is_used_when_the_runner_wrote_no_final_milestone(self):
        since = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime(STAMP)
        self._note("pu_start")
        case3_followup.record_primary_user(self.parent, "CO-0844", "add_rejected", datetime.now(timezone.utc), "example.org")
        self.runs["CO-0844"] = {"running": False, "stage": "primary_user_not_done", "since": since,
                                "process": self._process(alive=False)}
        html = self.d.primary_user_result_html(self.d.primary_user_run_state("CO-0844"), "add_rejected", None, self.parent,
                                               "CO-0844")
        self.assertIn(primary.RESULT_TEXT["add_rejected"], html)

    def test_without_a_run_the_newest_record_shows_as_a_banner(self):
        html = self.d.primary_user_result_html(None, primary.BY_HAND, "written_verified", self.parent, "CO-0844")
        self.assertIn("Confirmed by hand", html)
        self.assertIn("banner-ok", html)
        self.assertEqual(self.d.primary_user_result_html(None, None, None, self.parent, "CO-0844"), "")

    def test_single_flight_launch(self):
        launches = []

        def fake_launch(reference):
            process = self._process()
            launches.append(reference)
            self.runs[reference] = {"running": True, "stage": None, "since": datetime.now(timezone.utc).strftime(STAMP),
                                    "process": process}
            return process

        with mock.patch.object(self.d, "_launch_primary_user", side_effect=fake_launch):
            self.assertEqual(self.d._post_primary_user(create_ui.PRIMARY_USER_PATH, "CO-0844", {}), "ok")  # noqa: SLF001
            self.assertEqual(self.d._post_primary_user(create_ui.PRIMARY_USER_PATH, "CO-0844", {}),  # noqa: SLF001
                             "primary_user_running")
        self.assertEqual(launches, ["CO-0844"])
        self.assertIn("already running", create_ui.gate_text("primary_user_running"))

    def test_run_end_clears_the_cached_views(self):
        self.d._RECENT_CACHE["at"] = 123.0  # noqa: SLF001
        self.d._AUDIT_CACHE["at"] = 123.0  # noqa: SLF001
        since = datetime.now(timezone.utc).strftime(STAMP)
        self.runs["CO-0844"] = {"running": True, "stage": None, "since": since, "process": self._process(alive=False)}
        with mock.patch.object(self.d, "_primary_user_stage_move", return_value="primary_user_not_done"):
            self.d._follow_primary_user("CO-0844", self._process(alive=False))  # noqa: SLF001
        self.assertEqual(self.d._RECENT_CACHE["at"], 0.0)  # noqa: SLF001
        self.assertEqual(self.d._AUDIT_CACHE["at"], 0.0)  # noqa: SLF001
        self.assertEqual(self.runs["CO-0844"]["since"], since)  # the run keeps its start: its progress stays shown
        self.assertEqual(self.runs["CO-0844"]["stage"], "primary_user_not_done")
        self.assertFalse(self.runs["CO-0844"]["running"])


if __name__ == "__main__":
    unittest.main()
