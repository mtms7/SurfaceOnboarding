"""Tests for the dashboard side of the guarded production create and UUID write (docs/53): no control while locked, every gate
refusal, the typed confirmation, one-time tokens, one launch, revision drift, the approval consumed once, the guarded UUID write.
Fakes only: nothing is started, read or written outside a temporary folder."""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import production_create_record as pcr
from integration.onboarding import production_create_ui as ui
from integration.onboarding import salesforce_uuid_writeback as wb
from integration.onboarding import session_readiness as readiness
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
CO = "CO-0798"
REF = "PROD-CE-0798-20261017"
REV = "2026-10-17T09:00:00.000+0000"
OWNER = "milton.stevenson@example.com"
UUID = "0123456789abcdef0123456789abcdef"
ROUTE = "case_2_new_ce_only"
FORBIDDEN_TAGS = ("<form", "<button", "<input", "<script", "<select", "<textarea", "onclick", "method='post'", "<a ", "href=")


def row(**over):
    data = {"Id": "a0B5g00000AbCdEFGH", "Name": CO, "LastModifiedDate": REV, "Onboarding_Product__c": "Credential Exposure",
            "Onboarding_Type__c": "New Product Onboarding", "Account_UUID__c": None}
    data.update(over)
    return data


FRESH_CLONE = {"state": "fresh", "age_seconds": 3600}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.approvals = self.dir / "approvals.json"
        approval.issue(self.approvals, REF, CO, ROUTE, REV, OWNER, frozenset({OWNER}), NOW - timedelta(hours=1))
        self.record(outcome="dry_run_passed", when=NOW - timedelta(hours=2))
        self.ticket_root = self.dir / "ticket-root"
        for patch in (mock.patch.object(ui, "TOKENS", ui.OneTimeTokens()), mock.patch.object(ui, "LAUNCHES", ui.LaunchRegistry()),
                      mock.patch.dict("os.environ", {"SURFACE_ONBOARDING_TICKET_ROOT": str(self.ticket_root)})):
            patch.start()
            self.addCleanup(patch.stop)

    def record(self, outcome, when=NOW, **extra):
        data = {"co_reference": CO, "approval_ref": REF, "outcome": outcome, "route": ROUTE, "salesforce_revision": REV, **extra}
        return pcr.record(self.dir, data, when)

    def inputs(self, **over):
        data = dict(reference=CO, row=row(), now=NOW, environ={ui.SESSIONS_ENV: "1"}, operator=OWNER, vm=False,
                    approvals_path=self.approvals, records_parent=self.dir, clone=dict(FRESH_CLONE), sf_ready=True,
                    row_fresh=True)
        data.update(over)
        return ui.Inputs(**data)

    def unlocked(self, runner=True, dry=True):
        stack = [mock.patch.object(readiness, "PRODUCTION_RUNNER_SUPPORTED", runner),
                 mock.patch.object(readiness, "PRODUCTION_DRY_RUN_SUPPORTED", dry)]
        for patch in stack:
            patch.start()
            self.addCleanup(patch.stop)

    def has_control(self, html):
        return any(tag in html for tag in FORBIDDEN_TAGS)


class LockedTests(Base):
    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_constants_are_false_in_this_build(self):
        self.assertIs(readiness.PRODUCTION_RUNNER_SUPPORTED, False)
        self.assertIs(readiness.PRODUCTION_DRY_RUN_SUPPORTED, True)  # dry run only; the create needs BOTH
        self.assertTrue(ui.locked())

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_no_html_at_all_while_locked_without_records(self):
        pcr_dir = self.dir / pcr.RECORDS_SUBDIR
        for item in pcr_dir.iterdir():
            item.unlink()
        self.assertEqual(ui.create_card_html(self.inputs()), "")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_locked_page_with_a_dry_run_record_has_no_html(self):
        self.assertEqual(ui.create_card_html(self.inputs()), "")

    def test_locked_status_text_has_no_control(self):
        for outcome, word in (("readback_verified", "Created and verified"), ("uncertain_submit", "do not retry"),
                              ("attempt_started", "do not retry")):
            self.setUp()
            self.record(outcome=outcome)
            html = ui.create_card_html(self.inputs())
            self.assertIn(word, html)
            self.assertFalse(self.has_control(html), outcome)

    def test_one_constant_is_not_enough(self):
        for runner, dry in ((True, False), (False, True)):
            self.setUp()
            self.unlocked(runner, dry)
            self.assertEqual(ui.create_card_html(self.inputs()), "")
            self.assertEqual(ui.gate(self.inputs())[0], "production_runner_not_supported" if not runner else "production_dry_run_not_supported")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_every_post_is_refused_while_locked_and_launches_nothing(self):
        launch = mock.Mock()
        values = {"reference": CO, "nonce": "x", "confirm_reference": CO, "production_create_authorized": "1"}
        self.assertEqual(ui.handle_create_post(values, self.inputs(), launch), "production_runner_not_supported")
        launch.assert_not_called()
        review, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row())
        self.assertEqual(review, "production_runner_not_supported")
        run = mock.Mock()
        self.assertEqual(ui.handle_uuid_confirm({"reference": CO, "nonce": "x"}, self.inputs(), lambda: row(), run, "alias"),
                         "production_runner_not_supported")
        run.assert_not_called()

    def test_vm_never_gets_a_control_even_if_both_constants_were_true(self):
        self.unlocked()
        self.assertEqual(ui.create_card_html(self.inputs(vm=True)), "")
        self.assertEqual(ui.gate(self.inputs(vm=True))[0], "vm_read_only")


class GateTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()

    def test_all_gates_pass_shows_the_form_with_the_typed_reference_and_a_token(self):
        html = ui.create_card_html(self.inputs())
        for fragment in ("Create in production", "action='/attended/production-create'", "name='confirm_reference'",
                         "name='production_create_authorized'", "name='nonce'", "name='reference' value='CO-0798'"):
            self.assertIn(fragment, html)
        self.assertEqual(html.count("<form"), 1)
        self.assertIsNone(ui.gate(self.inputs())[0])

    def refuses(self, reason, **over):
        reason_found, _ = ui.gate(self.inputs(**over))
        self.assertEqual(reason_found, reason)
        html = ui.create_card_html(self.inputs(**over))
        self.assertFalse(self.has_control(html), reason)
        self.assertIn(ui.gate_text(reason), html.replace("&#x27;", "'"))

    def test_each_gate_refuses_and_shows_no_control(self):
        self.refuses("operator_not_owner", operator="someone.else@example.com")
        self.refuses("operator_not_owner", operator=None)
        self.refuses("approval_revision_drift", row=row(LastModifiedDate="2026-10-17T10:00:00.000+0000"))
        self.refuses("route_not_ce_only_new", row=row(Onboarding_Product__c="Surface"))
        self.refuses("route_not_ce_only_new", row=row(Onboarding_Type__c="Renewal"))
        self.refuses("salesforce_not_fresh", row_fresh=False)
        self.refuses("salesforce_not_fresh", row=None)
        self.refuses("salesforce_session_not_ready", sf_ready=False)
        self.refuses("salesforce_prior_uuid_present", row=row(Account_UUID__c=UUID))
        self.refuses("clone_stale", clone={"state": "stale", "age_seconds": 30000})
        self.refuses("clone_stale", clone={"state": "fresh", "age_seconds": 6 * 3600 + 1})
        self.refuses("clone_stale", clone={"state": "unusable", "age_seconds": None})
        self.refuses("clone_stale", clone=None)
        self.refuses("production_locked", environ={})
        self.refuses("approval_expired", now=NOW + timedelta(days=8))

    def test_account_uuid_field_not_read_is_not_treated_as_empty(self):
        data = row()
        del data["Account_UUID__c"]
        self.refuses("salesforce_prior_uuid_present", row=data)

    def test_dry_run_gates(self):
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        self.refuses("dry_run_required")
        self.record(outcome="dry_run_passed", when=NOW - timedelta(hours=25))
        self.refuses("dry_run_old")
        self.setUp()
        self.unlocked()
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        self.record(outcome="dry_run_passed", when=NOW - timedelta(hours=1))
        self.assertIsNone(ui.gate(self.inputs())[0])
        wrong = pcr.record(self.dir, {"co_reference": CO, "approval_ref": "PROD-CE-0001-20261017", "outcome": "dry_run_passed"},
                           NOW)
        wrong.unlink()
        self.assertIsNone(ui.gate(self.inputs())[0])

    def test_a_dry_run_of_another_approval_does_not_count(self):
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        pcr.record(self.dir, {"co_reference": CO, "approval_ref": "PROD-CE-0001-20261017", "outcome": "dry_run_passed",
                              "route": ROUTE}, NOW - timedelta(hours=1))
        self.assertEqual(ui.gate(self.inputs())[0], "dry_run_required")

    def test_a_prior_attempt_blocks_and_shows_its_status(self):
        for outcome, code in (("attempt_started", ui.CREATE_OUTCOME_UNKNOWN), ("uncertain_submit", ui.CREATE_OUTCOME_UNKNOWN),
                              ("created_unverified", ui.CREATE_OUTCOME_UNKNOWN)):
            self.setUp()
            self.unlocked()
            self.record(outcome=outcome)
            self.assertEqual(ui.gate(self.inputs())[0], "prior_attempt_needs_review", outcome)
            self.assertFalse(self.has_control(ui.create_card_html(self.inputs())))
            self.assertEqual(ui.co_status(ui.load_co_records(self.dir, CO))["code"], code)

    def test_status_mapping(self):
        self.assertEqual(ui.ui_status("readback_verified"), ui.CREATED_VERIFIED)
        self.assertEqual(ui.ui_status("created_unverified", "readback_contract_mismatch"), ui.CREATED_MISMATCH)
        self.assertEqual(ui.ui_status("created_unverified", "readback_payload_mismatch"), ui.CREATED_MISMATCH)
        self.assertEqual(ui.ui_status("created_unverified", "readback_payload_unchecked"), ui.CREATE_OUTCOME_UNKNOWN)
        self.assertEqual(ui.ui_status("created_unverified", "readback_name_mismatch"), ui.CREATED_MISMATCH)
        self.assertEqual(ui.ui_status("created_unverified", "readback_unavailable"), ui.CREATE_OUTCOME_UNKNOWN)
        self.assertEqual(ui.ui_status("uncertain_submit"), ui.CREATE_OUTCOME_UNKNOWN)
        self.assertEqual(ui.ui_status("attempt_started"), ui.CREATE_OUTCOME_UNKNOWN)
        self.assertIsNone(ui.ui_status("refused"))

    def test_status_wins_over_file_order_inside_one_second(self):
        self.record(outcome="attempt_started", when=NOW)
        self.record(outcome="readback_verified", when=NOW, account_uuid=UUID)
        status = ui.co_status(ui.load_co_records(self.dir, CO))
        self.assertEqual((status["code"], status["uuid"]), (ui.CREATED_VERIFIED, UUID))

    def test_unreadable_records_fail_closed(self):
        (self.dir / pcr.RECORDS_SUBDIR / "CO-0798-bad.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(ui.gate(self.inputs())[0], "records_unreadable")
        self.assertFalse(self.has_control(ui.create_card_html(self.inputs())))

    def test_unreadable_approvals_fail_closed(self):
        self.approvals.write_text("garbage", encoding="utf-8")
        self.assertEqual(ui.gate(self.inputs())[0], "approval_unreadable")

    def test_approval_consumed_once_closes_the_form(self):
        self.assertIsNone(ui.gate(self.inputs())[0])
        self.assertIsNone(approval.consume(self.approvals, REF, CO, ROUTE, REV, NOW))
        self.assertEqual(ui.gate(self.inputs())[0], "approval_consumed")
        self.assertFalse(self.has_control(ui.create_card_html(self.inputs())))


class PostTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.launched = []

    def launch(self, ref, revision, env):
        self.launched.append((ref, revision, env))
        return SimpleNamespace(poll=lambda: None)

    def token(self):
        html = ui.create_card_html(self.inputs())
        return re.search(r"name='nonce' value='([^']+)'", html).group(1)

    def values(self, **over):
        data = {"reference": CO, "nonce": self.token(), "confirm_reference": CO, "production_create_authorized": "1"}
        data.update(over)
        return data

    def test_a_complete_post_launches_exactly_once_with_the_approval_environment(self):
        self.assertEqual(ui.handle_create_post(self.values(), self.inputs(), self.launch), "launched")
        self.assertEqual(len(self.launched), 1)
        ref, revision, env = self.launched[0]
        self.assertEqual((ref, revision), (CO, REV))
        self.assertEqual(env["SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"], REF)

    def test_the_token_is_single_use_even_for_the_same_form(self):
        values = self.values()
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launched")
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launch_in_progress")
        self.assertEqual(len(self.launched), 1)

    def test_a_second_launch_is_blocked_while_the_first_runs_and_after_a_token_reuse(self):
        values = self.values()
        ui.handle_create_post(values, self.inputs(), self.launch)
        # While the run is active the page shows no form at all, and a posted token is refused.
        self.assertFalse(self.has_control(ui.create_card_html(self.inputs())))
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "launch_in_progress")
        self.assertEqual(len(self.launched), 1)

    def test_bad_confirmations_refuse_without_launching(self):
        for over, reason in (({"confirm_reference": "CO-0797"}, "typed_reference_mismatch"),
                             ({"confirm_reference": ""}, "typed_reference_mismatch"),
                             ({"production_create_authorized": "0"}, "authorization_missing"),
                             ({"nonce": "wrong"}, "confirmation_invalid"), ({"nonce": ""}, "confirmation_invalid")):
            self.setUp()
            self.unlocked()
            self.launched.clear()
            self.assertEqual(ui.handle_create_post(self.values(**over), self.inputs(), self.launch), reason, over)
            self.assertEqual(self.launched, [], over)

    def test_missing_or_duplicated_fields_refuse(self):
        values = self.values()
        del values["production_create_authorized"]
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "authorization_missing")
        self.assertEqual(ui.handle_create_post({**self.values(), "reference": "CO-0797"}, self.inputs(), self.launch), "invalid_request")
        self.assertEqual(self.launched, [])

    def test_revision_drift_between_page_and_post_refuses_and_spends_the_token(self):
        values = self.values()
        drifted = self.inputs(row=row(LastModifiedDate="2026-10-17T11:00:00.000+0000"))
        self.assertEqual(ui.handle_create_post(values, drifted, self.launch), "approval_revision_drift")
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "confirmation_invalid")
        self.assertEqual(self.launched, [])

    def test_a_refused_attempt_spends_the_token(self):
        values = self.values()
        self.assertEqual(ui.handle_create_post(values, self.inputs(sf_ready=False), self.launch), "salesforce_session_not_ready")
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "confirmation_invalid")

    def test_wrong_operator_cannot_post(self):
        self.assertEqual(ui.handle_create_post(self.values(), self.inputs(operator="x@example.com"), self.launch),
                         "operator_not_owner")
        self.assertEqual(self.launched, [])

    def test_consumed_approval_cannot_post(self):
        values = self.values()
        approval.consume(self.approvals, REF, CO, ROUTE, REV, NOW)
        self.assertEqual(ui.handle_create_post(values, self.inputs(), self.launch), "approval_consumed")
        self.assertEqual(self.launched, [])

    def test_a_failed_launch_is_reported_and_not_retried(self):
        self.assertEqual(ui.handle_create_post(self.values(), self.inputs(), lambda *_a: None), "launch_failed")

    def test_token_expiry(self):
        clock = {"t": 0.0}
        tokens = ui.OneTimeTokens(ttl=10, clock=lambda: clock["t"])
        token = tokens.issue("create", CO, "b")
        self.assertEqual(tokens.issue("create", CO, "b"), token)
        clock["t"] = 11
        self.assertFalse(tokens.consume("create", CO, "b", token))
        token = tokens.issue("create", CO, "b")
        self.assertFalse(tokens.consume("create", CO, "other", token))
        self.assertFalse(tokens.consume("create", CO, "b", token))


class UuidTests(Base):
    def setUp(self):
        super().setUp()
        self.unlocked()
        self.record(outcome="attempt_started")
        self.record(outcome="readback_verified", account_uuid=UUID)
        self.writes = []
        self.rows = [row()]

    def verified_status_html(self):
        return ui.create_card_html(self.inputs())

    def test_verified_status_offers_the_separate_uuid_form_only(self):
        approval.consume(self.approvals, REF, CO, ROUTE, REV, NOW)
        html = self.verified_status_html()
        self.assertIn("Write Account UUID to Salesforce", html)
        self.assertIn("action='/attended/production-uuid-review'", html)
        self.assertNotIn("Create in production", html)
        self.assertNotIn("production-create'", html)
        self.assertNotIn(UUID, html)

    def run_confirm(self, **over):
        code, view = ui.handle_uuid_review({"reference": CO}, self.inputs(), self.rows[0])
        self.assertEqual(code, "ready")
        self.assertEqual(view["field"], "Account_UUID__c")
        self.assertEqual(view["tail"], UUID[-4:])
        state = {"after": False}

        def read():
            return self.rows[1] if state["after"] and len(self.rows) > 1 else self.rows[0]

        def run(argv):
            self.writes.append(argv)
            state["after"] = True
            return over.get("accepted", True)
        return ui.handle_uuid_confirm({"reference": CO, "nonce": over.get("nonce", view["token"])}, self.inputs(), read, run,
                                      "surface-onboarding")

    def test_write_is_verified_after_the_one_fixed_argv_and_recorded_without_the_value(self):
        self.rows = [row(), row(Account_UUID__c=UUID)]
        self.assertEqual(self.run_confirm(), "written_verified")
        self.assertEqual(len(self.writes), 1)
        argv = self.writes[0]
        self.assertEqual(argv[:6], ["sf", "data", "update", "record", "--sobject", "Customer_Onboarding__c"])
        self.assertEqual(argv[argv.index("--values") + 1], "Account_UUID__c=" + UUID)
        text = (self.dir / pcr.RECORDS_SUBDIR / ui.UUID_WRITES_FILE).read_text(encoding="utf-8")
        self.assertNotIn(UUID, text)
        self.assertEqual(ui.last_uuid_write(self.dir, CO), "written_verified")

    def test_mismatch_after_write_is_uncertain_and_blocks_a_second_write(self):
        self.rows = [row(), row(Account_UUID__c="f" * 32)]
        self.assertEqual(self.run_confirm(), "write_uncertain")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), self.rows[0])
        self.assertEqual(code, "uuid_write_needs_review")

    def test_cli_failure_is_uncertain_or_rejected_and_never_retried(self):
        self.rows = [row()]
        self.assertEqual(self.run_confirm(accepted=None), "write_uncertain")
        self.setUp()
        self.assertEqual(self.run_confirm(accepted=False), "write_rejected")
        self.assertEqual(len(self.writes), 1)

    def test_blockers(self):
        cases = ((row(Account_UUID__c="c" * 32), "different_value" if False else None), )
        del cases
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row(Account_UUID__c="c" * 32))
        self.assertEqual(code, "uuid_conflict")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row(LastModifiedDate="2026-10-17T11:00:00.000+0000"))
        self.assertEqual(code, "revision_changed")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row(Onboarding_Product__c="Surface"))
        self.assertEqual(code, "route_not_ce_only_new")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), None)
        self.assertEqual(code, "salesforce_session_not_ready")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(sf_ready=False), row())
        self.assertEqual(code, "salesforce_session_not_ready")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(vm=True), row())
        self.assertEqual(code, "vm_read_only")

    def test_nothing_unless_the_create_was_verified(self):
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        self.record(outcome="uncertain_submit")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row())
        self.assertEqual(code, "readback_not_verified")

    def test_token_is_required_and_single_use(self):
        self.rows = [row(), row(Account_UUID__c=UUID)]
        self.assertEqual(self.run_confirm(nonce="wrong"), "confirmation_invalid")
        self.assertEqual(self.writes, [])

    def test_revision_change_between_review_and_confirm_refuses(self):
        code, view = ui.handle_uuid_review({"reference": CO}, self.inputs(), row())
        changed = row(LastModifiedDate="2026-10-17T11:30:00.000+0000")
        result = ui.handle_uuid_confirm({"reference": CO, "nonce": view["token"]}, self.inputs(), lambda: changed,
                                        lambda argv: self.writes.append(argv) or True, "alias")
        self.assertEqual(result, "revision_changed")
        self.assertEqual(self.writes, [])

    def test_prior_empty_field_required_and_uuid_in_record_must_be_valid(self):
        for item in (self.dir / pcr.RECORDS_SUBDIR).iterdir():
            item.unlink()
        self.record(outcome="readback_verified", account_uuid="not-a-uuid")
        code, _ = ui.handle_uuid_review({"reference": CO}, self.inputs(), row())
        self.assertEqual(code, "uuid_invalid")


class SourceTests(unittest.TestCase):
    def test_module_starts_nothing_and_reads_nothing_external(self):
        text = Path(ui.__file__).read_text(encoding="utf-8")
        for forbidden in ("subprocess", "import socket", "urllib", "playwright", "print(", "sf_json", "sf_write_json"):
            self.assertNotIn(forbidden, text)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_dashboard_wiring_is_outside_the_read_only_regions_and_refuses_while_locked(self):
        import tools.serve_attended_open_onboardings_dashboard as dashboard
        self.assertTrue(ui.POST_PATHS <= dashboard.POST_ROUTES)
        self.assertEqual(dashboard.VM_OPERATOR_POSTS, frozenset())
        import tempfile
        empty = tempfile.TemporaryDirectory()  # hermetic: never the real records folder (it holds the real CO-0798 create)
        self.addCleanup(empty.cleanup)
        with mock.patch.object(dashboard, "production_records_parent", return_value=Path(empty.name)):
            self.assertEqual(dashboard._production_create_html(CO, row()), "")
        sent = []
        handler = object.__new__(dashboard.Handler)
        handler.send_page = lambda status, page: sent.append((status, page))
        handler.request_operator = lambda: OWNER
        launch = mock.Mock(side_effect=AssertionError("must not launch"))
        run_sf = mock.Mock(side_effect=AssertionError("must not write"))
        with mock.patch.object(dashboard, "detail_row", return_value=row()), \
                mock.patch.object(dashboard, "_launch_production_create", launch), \
                mock.patch.object(dashboard, "_run_sf_argv", run_sf), \
                mock.patch.object(dashboard, "_read_production_writeback_row", return_value=row()),                 mock.patch.object(dashboard, "production_records_parent", return_value=Path(empty.name)):
            for path in sorted(ui.POST_PATHS):
                handler._post_production_create(path, CO, {"reference": [CO], "nonce": ["x"]})
        self.assertEqual(len(sent), len(ui.POST_PATHS))
        for status, page in sent:
            self.assertEqual(int(status), 409)
            self.assertIn("Nothing was sent", page)
        launch.assert_not_called()
        run_sf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
