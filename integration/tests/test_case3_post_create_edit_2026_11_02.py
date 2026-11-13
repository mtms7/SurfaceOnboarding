"""Owner go 2026-11-02 (CO-0757): after a verified Case 3 create, ONE Edit Account sets the Operator ("<Technical Advisor>
Operator") and Maximum scan Duration 90 (the owner's HARs: right after the create the row had campaignsTimeoutInHours null and
operatorAccounts null; after the owner's Edit Account it had 90 and "Yusuf Hassan Operator"). The guard lets exactly one
POST /account/{created id}/edit through; the row is re-read and every field the create verified must still match.
Hermetic: fake pages, fake replies; nothing reaches BackOffice or Salesforce.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import case3_followup as fu
from integration.onboarding import production_case3_edit as edit
from integration.onboarding import production_create_live as live
from integration.onboarding import production_create_record as records
from integration.tests import test_case3_live_runner as c3live
import tools.attended_ce_only_playwright as runner

ACCOUNT_ID = "6ae913859e2d0070a977542d"
OTHER_ID = "64f5c28660847c632262f501"
UUID = "087258c0073a46d687c3ebe6d02d31f6"
NAME, DOMAIN = "uMngeni-uThukela Water", "uuw.co.za"
OPERATOR = "Yusuf Hassan Operator"
INTENDED = {"tenant_name": NAME, "primary_domain": DOMAIN}
EXPECTED = {"accountName": NAME, "accountDomain": DOMAIN, "scanningInterval": "MONTHLY"}
EDIT_URL = "https://app.pentera.io/api/v1/backoffice/account/{}/edit"


def row(timeout=None, operators=None, **over):
    base = {"id": ACCOUNT_ID, "accountUuid": UUID, "accountName": NAME, "accountDomain": DOMAIN, "scanningInterval": "MONTHLY",
            "campaignsTimeoutInHours": timeout,
            "operatorAccounts": None if operators is None else [{"accountId": OTHER_ID, "operatorName": n} for n in operators]}
    base.update(over)
    return base


def reply(*rows, status=200):
    return {"status": status, "rows": list(rows), "total": len(rows)}


class RuleTests(unittest.TestCase):
    def test_operator_name_follows_the_technical_advisor(self):
        self.assertEqual(edit.operator_name("Yusuf Hassan"), OPERATOR)
        self.assertEqual(edit.operator_name("  Frank   Hoeffle "), "Frank Hoeffle Operator")
        for bad in ("", None, "x", "Yusuf Hassan Operator", "R2D2 Unit", "a@b.example"):
            with self.subTest(bad=bad):
                self.assertIsNone(edit.operator_name(bad))

    def test_before_the_edit(self):
        self.assertEqual(edit.before_edit(reply(row()), INTENDED, ACCOUNT_ID, OPERATOR), "ok")
        self.assertEqual(edit.before_edit(reply(row(90, [OPERATOR])), INTENDED, ACCOUNT_ID, OPERATOR), edit.ALREADY_SET)
        self.assertEqual(edit.before_edit(reply(row(None, ["Someone Operator"])), INTENDED, ACCOUNT_ID, OPERATOR),
                         "edit_operator_already_other")
        self.assertEqual(edit.before_edit(reply(row(id=OTHER_ID)), INTENDED, ACCOUNT_ID, OPERATOR), "edit_row_not_found")
        self.assertEqual(edit.before_edit(reply(), INTENDED, ACCOUNT_ID, OPERATOR), "edit_row_not_found")
        self.assertEqual(edit.before_edit(reply(status=401), INTENDED, ACCOUNT_ID, OPERATOR), "edit_session_expired")
        self.assertEqual(edit.before_edit(reply(row(operatorAccounts="x")), INTENDED, ACCOUNT_ID, OPERATOR),
                         "edit_row_unexpected")

    def test_after_the_edit(self):
        self.assertEqual(edit.after_edit(reply(row(90, [OPERATOR])), INTENDED, EXPECTED, ACCOUNT_ID, OPERATOR), edit.VERIFIED)
        self.assertEqual(edit.after_edit(reply(row(24, [OPERATOR])), INTENDED, EXPECTED, ACCOUNT_ID, OPERATOR),
                         "edit_values_not_kept")
        self.assertEqual(edit.after_edit(reply(row(90, ["Other Operator"])), INTENDED, EXPECTED, ACCOUNT_ID, OPERATOR),
                         "edit_values_not_kept")
        self.assertEqual(edit.after_edit(reply(row(90, [OPERATOR], scanningInterval="WEEKLY")), INTENDED, EXPECTED, ACCOUNT_ID,
                                         OPERATOR), "edit_other_fields_changed")
        self.assertEqual(edit.after_edit(reply(row(90)), INTENDED, EXPECTED, ACCOUNT_ID, None), edit.VERIFIED)
        self.assertEqual(edit.after_edit(reply(), INTENDED, EXPECTED, ACCOUNT_ID, OPERATOR), "edit_saved_unverified")

    def test_the_edit_reply(self):
        self.assertIsNone(edit.edit_response_problem("POST", ACCOUNT_ID, 200, ACCOUNT_ID))
        self.assertEqual(edit.edit_response_problem("POST", OTHER_ID, 200, ACCOUNT_ID), "edit_id_mismatch")
        self.assertEqual(edit.edit_response_problem("POST", ACCOUNT_ID, 403, ACCOUNT_ID), "edit_session_expired")
        self.assertEqual(edit.edit_response_problem("POST", ACCOUNT_ID, 500, ACCOUNT_ID), "edit_save_failed")


class Route:
    def __init__(self, url, method="POST"):
        self.request = SimpleNamespace(method=method, url=url, post_data=None)
        self.done = None

    def abort(self):
        self.done = "abort"

    def continue_(self):
        self.done = "continue"


class GuardTests(unittest.TestCase):
    def guard(self, created=True):
        g = live.LiveRequestGuard()
        g.create_used = created
        return g

    def send(self, g, account_id=ACCOUNT_ID):
        route = Route(EDIT_URL.format(account_id))
        g.handle(route)
        return route.done

    def test_an_edit_is_aborted_unless_armed(self):
        g = self.guard()
        self.assertEqual(self.send(g), "abort")
        self.assertEqual(g.edit_blocked, 1)
        self.assertIn("POST /api/v1/backoffice/account/{id}/edit", g.blocked_api_write_names())

    def test_exactly_one_edit_for_exactly_the_armed_id(self):
        g = self.guard()
        g.arm_edit(ACCOUNT_ID)
        self.assertEqual(self.send(g, OTHER_ID), "abort")
        self.assertEqual(self.send(g), "continue")
        self.assertEqual(self.send(g), "abort")  # a repeat is aborted and counted
        self.assertEqual(g.edit_blocked, 2)

    def test_no_edit_can_be_armed_before_the_create(self):
        g = self.guard(created=False)
        g.arm_edit(ACCOUNT_ID)
        self.assertEqual(self.send(g), "abort")

    def test_disarm_closes_it(self):
        g = self.guard()
        g.arm_edit(ACCOUNT_ID)
        g.disarm_edit()
        self.assertEqual(self.send(g), "abort")

    def test_only_the_exact_production_path(self):
        self.assertEqual(live.edit_request_id("POST", EDIT_URL.format(ACCOUNT_ID) + "?x=1"), ACCOUNT_ID)
        self.assertIsNone(live.edit_request_id("GET", EDIT_URL.format(ACCOUNT_ID)))
        self.assertIsNone(live.edit_request_id("POST", EDIT_URL.format(ACCOUNT_ID).replace("app.pentera.io", "evil.example")))
        self.assertIsNone(live.edit_request_id("POST", EDIT_URL.format(ACCOUNT_ID) + "/more"))


class Locator:
    def __init__(self, count=1, enabled=True, value="", labels=None):
        self._count, self.enabled, self.value, self.clicks = count, enabled, value, 0
        self.labels = labels or []

    def all_inner_texts(self):
        return list(self.labels)

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_enabled(self):
        return self.enabled

    def click(self, **_kw):
        self.clicks += 1

    def fill(self, value, **_kw):
        self.value = value

    def input_value(self):
        return self.value

    def nth(self, _i):
        return self


class FakeEditPage:
    """The Edit form: one Confirm, the operator select, and the app's edit reply when Confirm is clicked."""

    def __init__(self, guard, *, options=1, reply_id=ACCOUNT_ID, status=200, scan_sticks=True):
        self.guard, self.reply_id, self.status = guard, reply_id, status
        # 2026-11-13: the picker lists the options and picks the one that is the same person (accents etc. ignored).
        labels = {0: [], 1: [OPERATOR], 2: [OPERATOR, OPERATOR.replace(" Operator", " - Operator")]}[options]
        self.confirm, self.operator_input = Locator(), Locator()
        self.option = Locator(count=1, labels=labels)
        self.scan = Locator(value="24")
        if not scan_sticks:
            self.scan.fill = lambda value, **_kw: None
        self.sent = []

    def get_by_role(self, role, name=None, exact=False):
        return self.confirm if role == "button" else self.option

    def get_by_text(self, *_a, **_kw):
        return Locator(count=0)

    def locator(self, _selector):
        return self.operator_input

    def wait_for_timeout(self, _ms):
        pass

    @contextlib.contextmanager
    def expect_response(self, predicate, timeout=0):
        info = SimpleNamespace()
        yield info
        route = Route(EDIT_URL.format(self.reply_id))
        self.guard.handle(route)
        self.sent.append(route.done)
        response = SimpleNamespace(request=route.request, url=route.request.url, status=self.status)
        if route.done != "continue" or not predicate(response):
            raise TimeoutError("no reply")
        info.value = response


class EditRunnerTests(unittest.TestCase):
    def setUp(self):
        self.guard = live.LiveRequestGuard()
        self.guard.create_used = True
        self.replies = [reply(row()), reply(row(90, [OPERATOR]))]
        self.recorded = []
        self.cancelled = 0

        def cancel(_page):
            self.cancelled += 1
            return True

        reader = SimpleNamespace(readback=lambda _name: self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])
        for patch in (mock.patch.object(runner, "_pdr_reader", lambda *_a, **_k: reader),
                      mock.patch.object(runner, "_open_row_action", lambda *_a: None),
                      mock.patch.object(runner, "_spycloud_cancel", cancel),
                      mock.patch.object(runner, "_verify_operator_account_empty", lambda _p: None),
                      mock.patch.object(runner, "_pcr_selected_operators", lambda _p: [OPERATOR]),
                      mock.patch.object(runner, "_pdr_advanced_text", lambda page, _label: page.scan)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_edit(self, page, operator=OPERATOR):
        return runner._pcr_case3_edit(page, self.guard, INTENDED, EXPECTED, ACCOUNT_ID, operator, self.recorded.append)

    def test_the_edit_sets_both_values_confirms_once_and_verifies(self):
        page = FakeEditPage(self.guard)
        self.assertEqual(self.run_edit(page), edit.VERIFIED)
        self.assertEqual(page.operator_input.value, edit.search_text(OPERATOR))  # typed: the name without "Operator"
        self.assertEqual(page.option.clicks, 1)
        self.assertEqual(page.scan.value, "90")
        self.assertEqual((page.confirm.clicks, page.sent), (1, ["continue"]))
        self.assertEqual(self.recorded, ["write_started"])
        self.assertFalse(self.guard.edit_armed)

    def test_already_set_opens_nothing(self):
        self.replies = [reply(row(90, [OPERATOR]))]
        page = FakeEditPage(self.guard)
        self.assertEqual(self.run_edit(page), edit.ALREADY_SET)
        self.assertEqual(page.confirm.clicks, 0)

    def test_a_missing_or_ambiguous_operator_still_saves_the_hours(self):
        # Owner decision 2026-11-13: no single matching operator never blocks the rest; the hours are saved, the select stays
        # empty and the run reports it (the step turns it into edit_operator_unmatched: assign the operator by hand).
        for count in (0, 2):
            with self.subTest(options=count):
                self.guard = live.LiveRequestGuard()
                self.guard.create_used = True
                self.replies = [reply(row()), reply(row(90, []))]
                self.recorded = []
                page, diag = FakeEditPage(self.guard, options=count), {}
                result = runner._pcr_case3_edit(page, self.guard, INTENDED, EXPECTED, ACCOUNT_ID, OPERATOR,
                                                self.recorded.append, diag)
                self.assertEqual(result, edit.VERIFIED)
                self.assertEqual(diag.get("operator"), "unmatched")
                self.assertEqual(page.option.clicks, 0)
                self.assertEqual(page.operator_input.value, "")  # cleared, never left typed
                self.assertEqual((page.confirm.clicks, page.scan.value), (1, "90"))
        self.assertEqual(self.cancelled, 0)

    def test_a_value_that_does_not_stick_cancels(self):
        page = FakeEditPage(self.guard, scan_sticks=False)
        self.assertEqual(self.run_edit(page), "edit_fill_mismatch")
        self.assertEqual(page.confirm.clicks, 0)

    def test_an_edit_for_another_tenant_is_blocked_by_the_guard(self):
        page = FakeEditPage(self.guard, reply_id=OTHER_ID)
        self.assertEqual(self.run_edit(page), "edit_id_mismatch")
        self.assertEqual(page.sent, ["abort"])

    def test_a_failed_save_is_reported(self):
        self.assertEqual(self.run_edit(FakeEditPage(self.guard, status=500)), "edit_save_failed")

    def test_values_not_kept_after_the_save(self):
        self.replies = [reply(row()), reply(row(24, [OPERATOR]))]
        self.assertEqual(self.run_edit(FakeEditPage(self.guard)), "edit_values_not_kept")

    def test_without_an_operator_only_the_duration_is_set(self):
        self.replies = [reply(row()), reply(row(90))]
        page = FakeEditPage(self.guard)
        self.assertEqual(self.run_edit(page, operator=None), edit.VERIFIED)
        self.assertEqual(page.operator_input.value, "")


class StepTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.parent = Path(tmp.name)

    def lines(self):
        path = self.parent / records.RECORDS_SUBDIR / fu.WRITES_FILE
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def step(self, result=edit.VERIFIED, ta="Yusuf Hassan", account_id=ACCOUNT_ID):
        with mock.patch.object(runner, "_pcr_case3_edit", return_value=result) as called:
            out = runner._pcr_case3_edit_step(object(), object(), self.parent, "CO-0757", INTENDED,
                                              {"account_id": account_id, "expected": EXPECTED},
                                              SimpleNamespace(technical_advisor=ta), lambda: c3live.NOW)
        return out, called

    def test_the_result_is_recorded_with_the_operator_named(self):
        out, called = self.step()
        self.assertEqual(out, edit.VERIFIED)
        self.assertEqual(called.call_args.args[5], OPERATOR)
        self.assertEqual([(l["step"], l["result"], l["operator"]) for l in self.lines()], [("bo_edit", edit.VERIFIED, "named")])

    def test_no_technical_advisor_sets_only_the_duration_and_says_so(self):
        out, called = self.step(ta="")
        self.assertEqual(out, "edit_operator_unknown")
        self.assertIsNone(called.call_args.args[5])

    def test_an_unknown_account_id_edits_nothing(self):
        out, called = self.step(account_id=None)
        self.assertEqual(out, "edit_account_id_unknown")
        called.assert_not_called()

    def test_a_crash_is_recorded_not_raised(self):
        with mock.patch.object(runner, "_pcr_case3_edit", side_effect=RuntimeError("boom")):
            out = runner._pcr_case3_edit_step(object(), object(), self.parent, "CO-0757", INTENDED,
                                              {"account_id": ACCOUNT_ID, "expected": EXPECTED},
                                              SimpleNamespace(technical_advisor="Yusuf Hassan"), lambda: c3live.NOW)
        self.assertEqual(out, "edit_failed")

    def test_the_card_shows_the_result(self):
        self.step()
        self.assertIn("read back", fu.edit_note(self.parent, "CO-0757"))
        out, _ = self.step(result="edit_operator_not_found")
        self.assertIn("did not finish (edit_operator_not_found)", fu.edit_note(self.parent, "CO-0757"))

    def test_a_create_without_an_edit_line_keeps_the_manual_steps(self):
        self.assertEqual(fu.edit_note(self.parent, "CO-0802"), fu.MANUAL_AFTER_TEXT)


class WiringTests(unittest.TestCase):
    def test_a_verified_case_3_create_runs_the_edit_once_in_the_same_page(self):
        h = c3live.C3Harness(self)
        with mock.patch.object(runner, "_pcr_case3_edit_step", return_value=edit.VERIFIED) as step:
            out = h.go()
        self.assertEqual(out["outcome"], "readback_verified", out)
        step.assert_called_once()
        self.assertTrue(step.call_args.args[5].get("account_id"))  # the verified readback's Account ID

    def test_a_refused_case_3_run_never_edits(self):
        h = c3live.C3Harness(self)
        with mock.patch.object(runner, "_pcr_case3_edit_step") as step:
            out = h.go(owner_go=False)
        self.assertNotEqual(out["outcome"], "readback_verified")
        step.assert_not_called()


if __name__ == "__main__":
    unittest.main()
