"""2026-11-13 (owner decision, CO-0845 HAR): the BackOffice Operator Account is the Salesforce Account's Technical Advisor.

CE-only gets it by the same post-create Edit Account as Case 3, with Maximum scan Duration 24 h (Case 3: 90 h); a missing or
unmatched operator never blocks the rest; existing COs get a "Set Operator from Technical Advisor" button. Fakes only: no
browser, no BackOffice, no Salesforce.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import tools.attended_ce_only_playwright as runner
import tools.serve_attended_open_onboardings_dashboard as dashboard
from integration.onboarding import case3_followup
from integration.onboarding import post_onboard_audit as audit
from integration.onboarding import production_case3_edit as edit
from integration.onboarding import production_create_record as records

_PROGRESS_DIR = tempfile.TemporaryDirectory()
_PROGRESS_PATCH = mock.patch.object(runner, "PROGRESS_PATH", Path(_PROGRESS_DIR.name) / "progress.json")


def setUpModule():  # the runner's progress milestones never touch the real progress file
    _PROGRESS_PATCH.start()


def tearDownModule():
    _PROGRESS_PATCH.stop()
    _PROGRESS_DIR.cleanup()


CO = "CO-0845"
ACCOUNT_ID = "6af7537a3eed5320ec76b0e6"
TA = "Alejandro Espinosa Álvarez"
LABEL = "Alejandro Espinosa Alvarez Operator"
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc)
INTENDED = {"tenant_name": "Antolin - CE Only", "primary_domain": "grupoantolin.com"}


def reply(hours=None, operators=None):
    row = {"id": ACCOUNT_ID, "accountName": INTENDED["tenant_name"], "accountDomain": INTENDED["primary_domain"],
           "accountUuid": "0" * 32, "campaignsTimeoutInHours": hours,
           "operatorAccounts": None if operators is None else [{"operatorName": n} for n in operators]}
    return {"status": 200, "rows": [row]}


class NameMatchTests(unittest.TestCase):
    def test_the_co_0845_accent_matches_the_backoffice_label(self):
        self.assertTrue(edit.same_person(LABEL, TA))
        self.assertEqual(edit.search_text(TA), "Alejandro Espinosa Alvarez")
        self.assertEqual(edit.operator_name(TA), TA + " Operator")  # an accented name is still a plain person name

    def test_variants_and_refusals(self):
        self.assertTrue(edit.same_person("Alex Rosniewski - Operator", "Alex Rosniewski"))
        self.assertFalse(edit.same_person("Alex Rosniewski Operator", "Alex Rosniewsky"))
        self.assertFalse(edit.same_person("Operator", ""))
        self.assertEqual(edit.person_key(None), "")

    def test_exactly_one_option(self):
        labels = ["Brandon Cathey Operator", LABEL, "Albert Bolumar Operator"]
        self.assertEqual(edit.pick_operator(labels, TA), (LABEL, "ok"))
        self.assertEqual(edit.pick_operator(["Brandon Cathey Operator"], TA), (None, "edit_operator_not_found"))
        self.assertEqual(edit.pick_operator([LABEL, "Alejandro Espinosa Álvarez - Operator"], TA),
                         (None, "edit_operator_ambiguous"))
        self.assertEqual(edit.pick_operator([LABEL, LABEL + " "], TA), (LABEL, "ok"))  # the same label twice is one option


class RouteHoursTests(unittest.TestCase):
    def test_ce_only_24_case3_90(self):
        self.assertEqual(edit.ROUTE_SCAN_HOURS, {"case_2_new_ce_only": 24, "case_3_combined_baseline": 90})
        self.assertIn("Duration 24", edit.result_text(edit.VERIFIED, 24))
        self.assertIn("Duration 90", edit.result_text(edit.VERIFIED))
        self.assertIn("Add Operator Account", edit.result_text(edit.UNMATCHED, 24))
        self.assertIn(edit.UNMATCHED, edit.WRITE_OUTCOMES)

    def test_before_and_after_use_the_hours_and_the_person_match(self):
        operator = edit.operator_name(TA)
        self.assertEqual(edit.before_edit(reply(), INTENDED, ACCOUNT_ID, operator, 24), "ok")
        self.assertEqual(edit.before_edit(reply(24, [LABEL]), INTENDED, ACCOUNT_ID, operator, 24), edit.ALREADY_SET)
        self.assertEqual(edit.before_edit(reply(24, ["Albert Bolumar Operator"]), INTENDED, ACCOUNT_ID, operator, 24),
                         "edit_operator_already_other")  # someone else is never replaced
        self.assertEqual(edit.before_edit(reply(90, [LABEL]), INTENDED, ACCOUNT_ID, operator, 24), "ok")  # hours differ
        self.assertEqual(edit.after_edit(reply(24, [LABEL]), INTENDED, {}, ACCOUNT_ID, LABEL, 24), edit.VERIFIED)
        self.assertEqual(edit.after_edit(reply(90, [LABEL]), INTENDED, {}, ACCOUNT_ID, LABEL, 24), "edit_values_not_kept")
        self.assertEqual(edit.after_edit(reply(24, []), INTENDED, {}, ACCOUNT_ID, None, 24), edit.VERIFIED)  # unmatched


class LiveReadTests(unittest.TestCase):
    def test_a_present_null_is_no_operator_a_missing_key_unknown(self):
        self.assertIs(audit.live_row_facts({"operatorAccounts": None})["operator_assigned"], False)
        self.assertIs(audit.live_row_facts({"operatorAccounts": [{"operatorName": LABEL}]})["operator_assigned"], True)
        self.assertIsNone(audit.live_row_facts({})["operator_assigned"])
        self.assertIsNone(audit.live_row_facts({"operatorAccounts": "odd"})["operator_assigned"])


class RecordedEditTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.parent = Path(folder.name)

    def write(self, result, when, operator="named"):
        case3_followup._record(self.parent, CO, edit.STEP, result, when, {"operator": operator})

    def test_our_verified_edit_beats_an_older_no_operator_reading(self):
        live_line = {"recorded_at": "2026-11-13T19:00:00Z", "ce": "done", "facts": {"operator_assigned": False}}
        self.assertEqual(audit.operator_edit_at(self.parent, CO), None)
        self.write("edit_verified", NOW)
        at = audit.operator_edit_at(self.parent, CO)
        item = audit.view("ce_only", live_line, None, None, operator_edited_at=at)
        row = next(c for c in item["checks"] if c["key"] == "operator")
        self.assertEqual((row["state"], row["source"]), (audit.DONE, "record"))
        newer = {**live_line, "recorded_at": "2026-11-13T21:00:00Z"}  # a later BackOffice reading wins again
        row = next(c for c in audit.view("ce_only", newer, None, None, operator_edited_at=at)["checks"] if c["key"] == "operator")
        self.assertEqual(row["state"], audit.OPEN)

    def test_only_a_done_named_edit_counts_and_the_newest_line_decides(self):
        self.write("edit_verified", NOW, operator="unknown")
        self.assertIsNone(audit.operator_edit_at(self.parent, CO))
        self.write("edit_verified", NOW + timedelta(minutes=1))
        self.write("edit_operator_unmatched", NOW + timedelta(minutes=2))
        self.assertIsNone(audit.operator_edit_at(self.parent, CO))

    def test_ce_note(self):
        self.assertIsNone(case3_followup.ce_edit_note(self.parent, CO))
        self.write(edit.UNMATCHED, NOW)
        self.assertIn("Duration 24", case3_followup.ce_edit_note(self.parent, CO))
        self.write("write_started", NOW + timedelta(minutes=1))
        self.assertIn("did not finish", case3_followup.ce_edit_note(self.parent, CO))


class EditStepTests(unittest.TestCase):
    def test_ce_step_passes_24_and_the_advisor_and_reports_unmatched(self):
        seen = {}

        def fake_edit(page, guard, intended, expected, account_id, operator, record, diag, hours, note=None):
            seen.update(operator=operator, hours=hours)
            diag["operator"] = "unmatched"
            return edit.VERIFIED
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(runner, "_pcr_case3_edit", fake_edit):
            result = runner._pcr_case3_edit_step(None, None, Path(folder), CO, INTENDED,
                                                 {"account_id": ACCOUNT_ID, "expected": {}}, None, lambda: NOW,
                                                 scan_hours=24, technical_advisor=TA)
            self.assertEqual(result, edit.UNMATCHED)
            self.assertEqual(seen, {"operator": TA + " Operator", "hours": 24})
            self.assertEqual(case3_followup._last(Path(folder), CO, edit.STEP), edit.UNMATCHED)

    def test_technical_advisor_read_is_one_fixed_query(self):
        queries = []

        def fake(query):
            queries.append(query)
            return [{"Name": CO, "Account__r": {"Delivery_Engineer__r": {"Name": TA}}}]
        with mock.patch.object(runner, "_sf_records", fake):
            self.assertEqual(runner._pcr_technical_advisor(CO), TA)
            self.assertEqual(runner._pcr_technical_advisor("bad'"), "")
        self.assertEqual(len(queries), 1)
        self.assertIn("Account__r.Delivery_Engineer__r.Name", queries[0])


class ButtonRunTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.parent = Path(folder.name)
        for patcher in (mock.patch.object(runner, "_launch_os_name", lambda: "nt"),):
            patcher.start()
            self.addCleanup(patcher.stop)

    def verified(self, route="case_2_new_ce_only"):
        from integration.onboarding import production_create_ui as create_ui

        record = {"co_reference": CO, "route": route, "outcome": "readback_verified", "account_id": ACCOUNT_ID,
                  "tenant_name": INTENDED["tenant_name"], "recorded_at": "2026-11-13T18:14:24Z"}
        for patcher in (mock.patch.object(create_ui, "load_co_records", lambda _p, _c: [record]),
                        mock.patch.object(create_ui, "co_status", lambda _h: {"code": create_ui.CREATED_VERIFIED})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_it(self, **kw):
        kw.setdefault("advisor", lambda _ref: TA)
        return json.loads(runner.run_production_operator_edit(CO, environ={}, now=NOW, records_parent=self.parent,
                                                               open_page=self.no_browser, **kw))["result"]

    @staticmethod
    @contextmanager
    def no_browser(*_a, **_k):
        raise AssertionError("no browser may open")
        yield

    def test_gates_before_any_browser(self):
        self.assertEqual(json.loads(runner.run_production_operator_edit("x", environ={}))["result"], "invalid_co_reference")
        self.assertEqual(json.loads(runner.run_production_operator_edit(CO, environ={"SURFACE_ONBOARDING_RUNTIME": "vm"}))
                         ["result"], "browser_launch_not_allowed")
        self.assertEqual(self.run_it(), "no_verified_create")
        self.verified()
        self.assertEqual(self.run_it(advisor=lambda _ref: ""), "technical_advisor_missing")
        case3_followup._record(self.parent, CO, edit.STEP, "write_started", NOW, {"operator": "named"})
        self.assertEqual(self.run_it(), "operator_edit_needs_review")

    def test_a_ce_only_run_edits_the_verified_tenant_with_24_hours(self):
        self.verified()
        seen = {}

        @contextmanager
        def page_opener(*_a, **_k):
            yield SimpleNamespace()

        def fake_edit(page, guard, intended, expected, account_id, operator, record, diag, hours, note=None):
            seen.update(intended=intended, account_id=account_id, operator=operator, hours=hours, expected=expected,
                        guard=type(guard).__name__)
            return edit.VERIFIED
        reader = SimpleNamespace(readback=lambda _name: {"status": 200, "rows": [
            {"id": ACCOUNT_ID, "accountName": INTENDED["tenant_name"], "accountDomain": INTENDED["primary_domain"],
             "scanningInterval": "NO_SCHEDULE"}]})
        with mock.patch.object(runner, "_pcr_case3_edit", fake_edit), \
                mock.patch.object(runner, "_pdr_reader", lambda *_a, **_k: reader), \
                mock.patch.object(runner.production_probe, "probe_target", lambda: None), \
                mock.patch("integration.onboarding.production_create_live.RenewalEditGuard.install", lambda self, page: None):
            result = json.loads(runner.run_production_operator_edit(CO, environ={}, now=NOW, records_parent=self.parent,
                                                                    open_page=page_opener, advisor=lambda _r: TA))
        self.assertEqual(result, {"result": edit.VERIFIED, "production_write": "performed",
                                  "salesforce_writeback": "not_performed"})
        self.assertEqual((seen["account_id"], seen["hours"], seen["operator"], seen["guard"]),
                         (ACCOUNT_ID, 24, TA + " Operator", "RenewalEditGuard"))
        self.assertEqual(seen["intended"], INTENDED)
        self.assertEqual(seen["expected"]["scanningInterval"], "NO_SCHEDULE")  # must not change
        self.assertEqual(case3_followup._last(self.parent, CO, edit.STEP), edit.VERIFIED)


class DashboardButtonTests(unittest.TestCase):
    def test_card_offers_set_operator_while_open_or_unknown(self):
        open_item = audit.view("ce_only", {"recorded_at": "2026-11-13T19:00:00Z", "facts": {"operator_assigned": False}},
                               None, None)
        unknown = audit.view("ce_only", None, None, None)
        done = audit.view("ce_only", {"recorded_at": "2026-11-13T19:00:00Z", "facts": {"operator_assigned": True}}, None, None)
        self.assertIn(audit.OPERATOR_EDIT_PATH, audit.card_html(open_item, CO, may_confirm=True))
        self.assertNotIn(audit.OPERATOR_CONFIRM_PATH, audit.card_html(open_item, CO, may_confirm=True))  # known "no": no mark
        self.assertIn(audit.OPERATOR_EDIT_PATH, audit.card_html(unknown, CO, may_confirm=True))
        self.assertNotIn(audit.OPERATOR_EDIT_PATH, audit.card_html(done, CO, may_confirm=True))
        self.assertNotIn(audit.OPERATOR_EDIT_PATH, audit.card_html(open_item, CO, may_confirm=False))
        self.assertEqual(dashboard.OPERATOR_EDIT_PATH, audit.OPERATOR_EDIT_PATH)
        self.assertIn(dashboard.OPERATOR_EDIT_PATH, dashboard._PER_CO_POSTS)

    def test_post_gates(self):
        with mock.patch.object(dashboard, "vm_mode", return_value=True):
            self.assertEqual(dashboard._post_operator_edit(CO), "vm_read_only")
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(audit, "verified_creates", return_value={}):
            self.assertEqual(dashboard._post_operator_edit(CO), "no_verified_create")
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(audit, "verified_creates", return_value={CO: {}}), \
                mock.patch.object(dashboard.create_ui.LAUNCHES, "busy", return_value=True):
            self.assertEqual(dashboard._post_operator_edit(CO), "launch_in_progress")
        with mock.patch.object(dashboard, "vm_mode", return_value=False), \
                mock.patch.object(audit, "verified_creates", return_value={CO: {}}), \
                mock.patch.object(dashboard.create_ui.LAUNCHES, "busy", return_value=False), \
                mock.patch.object(dashboard.create_ui.LAUNCHES, "claim", return_value=True) as claim:
            self.assertEqual(dashboard._post_operator_edit(CO), "ok")
        claim.assert_called_once()


class CheckboxListPickerTests(unittest.TestCase):
    """CO-0843 2026-11-13: the Edit form's operator list is a checkbox list (no role=option items) and the CE-only form shows
    no Maximum scan Duration; the first live click stopped with edit_scan_control_absent and left the form open."""

    def test_label_candidates(self):
        self.assertEqual(edit.label_candidates(TA), [TA + " Operator", TA + " - Operator", LABEL,
                                                     "Alejandro Espinosa Alvarez - Operator"])
        self.assertEqual(edit.label_candidates("Ryan Slosser Operator"), ["Ryan Slosser Operator", "Ryan Slosser - Operator"])
        self.assertEqual(edit.label_candidates(""), [])

    def make_page(self, on_page, scan_control=False):
        from integration.tests import test_case3_post_create_edit_2026_11_02 as c3

        guard = c3.live.LiveRequestGuard()
        guard.create_used = True
        page = c3.FakeEditPage(guard, options=0, reply_id=ACCOUNT_ID)  # the edit reply is for THIS tenant
        texts = {label: c3.Locator(count=1) for label in on_page}
        page.get_by_text = lambda text, exact=False: texts.get(text, c3.Locator(count=0))
        presses = []
        page.operator_input.press = lambda key, **_kw: presses.append(key)
        return guard, page, texts, presses, c3

    def run_edit(self, page, guard, c3, hours, replies, diag):
        reader = SimpleNamespace(readback=lambda _n: replies.pop(0) if len(replies) > 1 else replies[0])
        with mock.patch.object(runner, "_pdr_reader", lambda *_a, **_k: reader), \
                mock.patch.object(runner, "_open_row_action", lambda *_a: None), \
                mock.patch.object(runner, "_spycloud_cancel", lambda _p: True), \
                mock.patch.object(runner, "_verify_operator_account_empty", lambda _p: None), \
                mock.patch.object(runner, "_pcr_selected_operators", lambda _p: None), \
                mock.patch.object(runner, "_pdr_advanced_text", lambda _page, _label: None):
            return runner._pcr_case3_edit(page, guard, INTENDED, {}, ACCOUNT_ID, edit.operator_name(TA), lambda _r: None,
                                          diag, hours)

    def test_a_checkbox_list_label_is_picked_and_ce_saves_without_a_scan_control(self):
        guard, page, texts, presses, c3 = self.make_page([LABEL])
        diag = {}
        result = self.run_edit(page, guard, c3, 24, [reply(None, None), reply(24, [LABEL])], diag)
        self.assertEqual(result, edit.VERIFIED)
        self.assertEqual(texts[LABEL].clicks, 1)
        self.assertEqual((diag.get("option_items"), diag.get("candidate_labels"), diag.get("scan_control")),
                         ("0", "1", "absent_form_default"))
        self.assertEqual(page.confirm.clicks, 1)
        self.assertIn("Tab", presses)  # the list is closed after the pick

    def test_no_label_saves_the_hours_and_closes_the_list(self):
        guard, page, _texts, presses, c3 = self.make_page([])
        diag = {}
        result = self.run_edit(page, guard, c3, 24, [reply(None, None), reply(24, [])], diag)
        self.assertEqual((result, diag.get("operator")), (edit.VERIFIED, "unmatched"))
        self.assertIn("Tab", presses)
        self.assertEqual(page.operator_input.value, "")

    def test_case3_still_stops_without_the_scan_control(self):
        guard, page, _texts, _presses, c3 = self.make_page([LABEL])
        self.assertEqual(self.run_edit(page, guard, c3, 90, [reply(None, None)], {}), "edit_scan_control_absent")
        self.assertEqual(page.confirm.clicks, 0)


class OperatorProgressTests(unittest.TestCase):
    """Owner request 2026-11-13: a progress bar for the operator edit (button run and the edit after a create)."""

    def events(self, *steps):
        return [{"t": "2026-11-13T22:00:%02dZ" % i, "step": step, "outcome": outcome} for i, (step, outcome) in enumerate(steps)]

    def test_running_then_done(self):
        from integration.onboarding import operator_edit_progress as op

        running = op.progress(self.events(("op_start", "button"), ("op_signed_in", "ok"), ("op_tenant_found", "ok")),
                              running=True)
        self.assertEqual((running["status"], running["current"]), ("running", op.STEPS[1]))
        self.assertLess(running["percent"], 100)
        done = op.progress(self.events(("op_start", "button"), ("op_tenant_found", "ok"), ("op_form_open", "ok"),
                                       ("op_operator", "picked"), ("op_scan", "form_default"), ("op_saved", "ok"),
                                       ("op_verified", "edit_verified"), ("op_result", "edit_verified")), running=False)
        self.assertEqual((done["status"], done["percent"]), ("done", 100))
        self.assertIn("Duration 24", op.card_html(done, 24))

    def test_unmatched_is_partial_and_a_stop_names_the_open_form(self):
        from integration.onboarding import operator_edit_progress as op

        partial = op.progress(self.events(("op_start", "button"), ("op_tenant_found", "ok"), ("op_form_open", "ok"),
                                          ("op_operator", "unmatched"), ("op_scan", "form_default"), ("op_saved", "ok"),
                                          ("op_verified", "edit_verified"), ("op_result", edit.UNMATCHED)), running=False)
        self.assertEqual(partial["status"], "partial")
        self.assertIn(("Pick the Technical Advisor's operator", "failed"), partial["steps"])
        self.assertIn("Add Operator Account", op.card_html(partial, 24))
        stopped = op.progress(self.events(("op_start", "button"), ("op_tenant_found", "ok"), ("op_form_open", "ok"),
                                          ("op_scan", "absent"), ("op_result", "edit_scan_control_absent_form_left_open")),
                              running=False)
        self.assertEqual(stopped["status"], "failed")
        html = op.card_html(stopped)
        self.assertIn("left open", html)
        self.assertIn("Maximum scan Duration field was not found", html)

    def test_only_the_newest_run_and_a_silent_end(self):
        from integration.onboarding import operator_edit_progress as op

        events = self.events(("op_start", "button"), ("op_result", "edit_verified"), ("pu_start", "ok"),
                             ("op_start", "button"), ("op_signed_in", "ok"))
        self.assertEqual([e["step"] for e in op.current_run(events)], ["op_start", "op_signed_in"])
        ended = op.progress(events, running=False)
        self.assertEqual((ended["status"], ended["result"]), ("failed", "runner_ended_without_a_result"))

    def test_the_button_run_writes_its_milestones(self):
        from integration.onboarding import operator_edit_progress as op
        from integration.onboarding import run_progress

        self.assertTrue(set(run_progress.OPERATOR_EDIT_MILESTONES) <= run_progress.MILESTONE_STEPS)
        with tempfile.TemporaryDirectory() as folder:
            json.loads(runner.run_production_operator_edit(CO, environ={}, now=NOW, records_parent=Path(folder),
                                                           advisor=lambda _r: TA))
            with mock.patch.object(runner, "_launch_os_name", lambda: "nt"):
                runner.run_production_operator_edit(CO, environ={}, now=NOW, records_parent=Path(folder),
                                                    advisor=lambda _r: TA)
        run = op.current_run(run_progress.read_milestones(runner.PROGRESS_PATH, CO))
        self.assertEqual([e["step"] for e in run], ["op_start", "op_result"])
        self.assertEqual(run[-1]["outcome"], "no_verified_create")


try:
    from integration.tests.test_local_accounts_2026_11_12 import SUPER, VIEWER, LocalModeSandbox
except ImportError:  # pragma: no cover - CI installs cryptography
    LocalModeSandbox = None


if LocalModeSandbox is not None:
    class RealHandlerPostTests(LocalModeSandbox):
        """2026-11-13: the first click returned "Not found" (the paths were missing from POST_ROUTES). Through the real handler."""

        def test_both_operator_buttons_reach_their_handlers(self):
            for path, target in ((dashboard.OPERATOR_EDIT_PATH, "_post_operator_edit"),
                                 (dashboard.OPERATOR_CONFIRM_PATH, "_post_operator_confirm")):
                with self.subTest(path=path), mock.patch.object(dashboard, target, return_value="ok") as handler:
                    response = self.request("POST", path, self.session(SUPER), ("reference=" + CO).encode())
                    self.assertEqual((self.status(response), self.location(response)), (303, "/co/" + CO + "?env=prod"))
                    handler.assert_called_once()
                self.assertIn(path, dashboard.POST_ROUTES)

        def test_a_refusal_shows_the_reason_and_a_viewer_is_refused(self):
            with mock.patch.object(dashboard, "_post_operator_edit", return_value="launch_in_progress"):
                response = self.request("POST", dashboard.OPERATOR_EDIT_PATH, self.session(SUPER), ("reference=" + CO).encode())
            self.assertEqual(self.status(response), 409)
            self.assertIn(b"launch_in_progress", response)
            with mock.patch.object(dashboard, "_post_operator_edit") as handler:
                response = self.request("POST", dashboard.OPERATOR_EDIT_PATH, self.session(VIEWER), ("reference=" + CO).encode())
            self.assertEqual(self.status(response), 403)
            handler.assert_not_called()


if __name__ == "__main__":
    unittest.main()
