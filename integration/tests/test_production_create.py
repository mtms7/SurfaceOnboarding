"""Tests for the pure production CE-only create orchestrator. Every external side effect is a fake; nothing external runs."""

from __future__ import annotations

import ast
import copy
import dataclasses
import inspect
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from integration.onboarding import production_approval as approval
from integration.onboarding import production_create as pc
from integration.onboarding import production_create_record as pcr
from integration.onboarding import salesforce_uuid_writeback as wb
from integration.onboarding import session_readiness
from integration.onboarding.run_target import ProductionLocked, RunTarget
from integration.tests.locked_build import locked_create_build  # noqa: E402

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
UUID = "0123456789abcdef0123456789abcdef"
ROUTE = "case_2_new_ce_only"
REF = "PROD-CE-0797-20261016"
REF_ENV = "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"
CO_ROW = {"Onboarding_Product__c": "Credential Exposure", "Onboarding_Type__c": "New Product Onboarding",
          "Account_UUID__c": None}
DRY_RECORD = {"co_reference": "CO-0797", "approval_ref": REF, "outcome": "dry_run_passed"}


def make_inputs(**over):
    data = dict(
        co_reference="CO-0797", route=ROUTE, approval_ref=REF, approval_path=Path("unused.json"),
        source_revision="r29", source_revision_now="r29", now=NOW, environ={REF_ENV: REF},
        sf_email_domains=("patio.cl",), sf_source_consistent=True, prior_account_uuid=None,
        clone_collected_at=NOW - timedelta(hours=1), clone_matches=True,
        duplicate_found=False, live_paid_tenant_count=0,
        timing_allowed=True, csm_request_recorded=False, sessions_ready=True, schema_ok=True,
        expected_tenant_name="Grupo Patio", expected_domains=("patio.cl",), expected_account_id="",
        plan_summary="Credential Exposure, 1 year")
    data.update(over)
    data["environ"] = {**data["environ"], "SURFACE_ONBOARDING_TEST_HOOKS": "1"}
    return pc.CreateInputs(**data)


def target_ok(environ, now):
    return RunTarget("backoffice_production", "https://app.pentera.io", "p")


def target_locked(reason):
    def fake(environ, now):
        raise ProductionLocked(reason)
    return fake


def approval_ok(*args):
    return None


def gate_with(target=target_ok, check=approval_ok):
    return lambda inputs: pc.evaluate_gate(inputs, production_target_fn=target, approval_check_fn=check)


# (reason, mutation) in gate order. Each mutation breaks exactly one rule.
BREAKERS = [
    ("production_locked:production_runner_not_supported", {"_target": target_locked("production_runner_not_supported")}),
    ("approval_ref_mismatch", {"environ": {REF_ENV: "PROD-CE-0001-20261016"}}),
    ("approval_revision_drift", {"source_revision_now": "r30"}),
    ("approval_expired", {"_check": lambda *a: "approval_expired"}),
    ("route_not_ce_only_new", {"route": "case_1_surface_only"}),
    ("salesforce_email_domain_not_single", {"sf_email_domains": ("a.com", "b.com")}),
    ("salesforce_source_mismatch", {"sf_source_consistent": False}),
    ("salesforce_prior_uuid_present", {"prior_account_uuid": UUID}),
    ("clone_missing", {"clone_collected_at": None}),
    ("clone_stale", {"clone_collected_at": NOW - timedelta(hours=6, seconds=1)}),
    ("clone_mismatch", {"clone_matches": False}),
    ("duplicate_in_production", {"duplicate_found": True}),
    ("multiple_live_paid_tenants", {"live_paid_tenant_count": 2}),
    ("timing_window_not_met", {"timing_allowed": False, "csm_request_recorded": False}),
    ("sessions_not_ready", {"sessions_ready": False}),
    ("schema_unexpected", {"schema_ok": False}),
]


def apply(breakers):
    over = {}
    target, check = target_ok, approval_ok
    for _, mutation in breakers:
        for key, value in mutation.items():
            if key == "_target":
                target = value
            elif key == "_check":
                check = value
            else:
                over.setdefault(key, value)  # earlier breaker wins when two share a key
    return make_inputs(**over), target, check


def unlock_environ():
    """A fully valid session unlock: only the (False) runner flag is left to refuse."""
    record = {"approval_ref": REF, "issued_at": "2026-10-15T11:00:00Z", "expires_at": "2026-10-22T11:00:00Z"}
    return {**approval.unlock_environ(record), "SURFACE_ONBOARDING_PRODUCTION_SESSIONS": "1"}


class TestGate(unittest.TestCase):
    def test_clean_inputs_pass(self):
        self.assertIsNone(gate_with()(make_inputs()))

    def test_each_refusal_fires_alone(self):
        for reason, mutation in BREAKERS:
            inputs, target, check = apply([(reason, mutation)])
            self.assertEqual(gate_with(target, check)(inputs), reason, reason)

    def test_each_refusal_fires_in_order(self):
        for index, (reason, _) in enumerate(BREAKERS):
            inputs, target, check = apply(BREAKERS[index:])
            self.assertEqual(gate_with(target, check)(inputs), reason, reason)

    def test_evaluate_gate_returns_first_only_and_is_pure(self):
        calls = []

        def counting_check(*args):
            calls.append(args)
            return None
        inputs, target, _ = apply(BREAKERS)
        snapshot = copy.deepcopy(dataclasses.asdict(inputs))
        first = pc.evaluate_gate(inputs, production_target_fn=target, approval_check_fn=counting_check)
        again = pc.evaluate_gate(inputs, production_target_fn=target, approval_check_fn=counting_check)
        self.assertEqual(first, BREAKERS[0][0])
        self.assertEqual(again, first)
        self.assertEqual(calls, [], "a later gate must not run once an earlier one refused")
        self.assertEqual(dataclasses.asdict(inputs), snapshot, "evaluate_gate must not change its inputs")

    def test_docstring_states_the_actual_order(self):
        doc = inspect.getdoc(pc.evaluate_gate).lower()
        words = ("production target", "approval reference", "revision", "approval check", "route", "email domain",
                 "clone", "duplicate", "live paid", "timing", "sessions", "schema")
        positions = [doc.index(word) for word in words]
        self.assertEqual(positions, sorted(positions))

    def test_session_unlock_reference_must_equal_the_input_reference(self):
        for env in ({}, {REF_ENV: ""}, {REF_ENV: "PROD-CE-0001-20261016"}, {REF_ENV: REF + "x"}):
            self.assertEqual(gate_with()(make_inputs(environ=env)), "approval_ref_mismatch", env)

    def test_current_revision_missing_or_changed_refuses_even_with_a_permissive_check(self):
        for bad in (None, "", 5, "r30"):
            self.assertEqual(gate_with()(make_inputs(source_revision_now=bad)), "approval_revision_drift", bad)
        self.assertEqual(gate_with()(make_inputs(source_revision="r30")), "approval_revision_drift")

    def test_check_receives_the_current_revision(self):
        seen = []

        def check(path, ref, co, route, revision, now):
            seen.append(revision)
            return None
        pc.evaluate_gate(make_inputs(), production_target_fn=target_ok, approval_check_fn=check)
        self.assertEqual(seen, ["r29"])

    def test_timing_ok_by_csm_request_alone(self):
        inputs = make_inputs(timing_allowed=False, csm_request_recorded=True)
        self.assertIsNone(gate_with()(inputs))

    def test_clone_exactly_six_hours_is_fresh_future_is_stale(self):
        self.assertIsNone(gate_with()(make_inputs(clone_collected_at=NOW - timedelta(hours=6))))
        self.assertEqual(gate_with()(make_inputs(clone_collected_at=NOW + timedelta(minutes=1))), "clone_stale")
        self.assertEqual(gate_with()(make_inputs(clone_collected_at=datetime(2026, 10, 16, 11, 0))), "clone_stale")

    def test_one_live_paid_tenant_is_allowed_but_non_int_is_refused(self):
        self.assertIsNone(gate_with()(make_inputs(live_paid_tenant_count=1)))
        for bad in (None, "0", True, -1):
            self.assertEqual(gate_with()(make_inputs(live_paid_tenant_count=bad)), "multiple_live_paid_tenants")

    def test_non_true_flags_fail_closed(self):
        self.assertEqual(gate_with()(make_inputs(sf_source_consistent="yes")), "salesforce_source_mismatch")
        self.assertEqual(gate_with()(make_inputs(duplicate_found=None)), "duplicate_in_production")
        self.assertEqual(gate_with()(make_inputs(clone_matches=1)), "clone_mismatch")
        self.assertEqual(gate_with()(make_inputs(sessions_ready=1)), "sessions_not_ready")
        self.assertEqual(gate_with()(make_inputs(schema_ok=None)), "schema_unexpected")

    def test_email_domain_shapes(self):
        for bad in ((), ("a.com", "a.com"), ("not a domain",), ("patio",), "patio.cl", None):
            self.assertEqual(gate_with()(make_inputs(sf_email_domains=bad)), "salesforce_email_domain_not_single")


class TestLockedByDefault(unittest.TestCase):
    """The default target check is the real one. A fully valid unlock leaves only the runner flag, which must refuse."""

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_runner_flag_is_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_only_the_runner_flag_blocks_a_valid_unlock(self):
        reason = pc.evaluate_gate(make_inputs(environ=unlock_environ()), approval_check_fn=approval_ok)
        self.assertEqual(reason, "production_locked:production_runner_not_supported")

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_flag_true_would_open_that_gate_proving_the_flag_is_the_only_reason(self):
        with mock.patch.object(session_readiness, "PRODUCTION_RUNNER_SUPPORTED", True):
            reason = pc.evaluate_gate(make_inputs(environ=unlock_environ()), approval_check_fn=approval_ok)
        self.assertIsNone(reason)
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)

    def test_missing_unlock_values_lock_earlier(self):
        reason = pc.evaluate_gate(make_inputs(environ={REF_ENV: REF}), approval_check_fn=approval_ok)
        self.assertEqual(reason, "production_locked:production_sessions_flag_missing")
        env = {**unlock_environ(), "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_EXPIRES": "2026-10-16T11:00:00+00:00"}
        reason = pc.evaluate_gate(make_inputs(environ=env), approval_check_fn=approval_ok)
        self.assertEqual(reason, "production_locked:production_approval_expired")


class TestRealApprovalCodes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "approvals.json"
        approval.issue(self.path, REF, "CO-0797", ROUTE, "r29", "milton", frozenset({"milton"}),
                       NOW - timedelta(hours=1))

    def gate(self, **over):
        return pc.evaluate_gate(make_inputs(approval_path=self.path, **over), production_target_fn=target_ok)

    def test_valid(self):
        self.assertIsNone(self.gate())

    def test_codes_pass_through(self):
        self.assertEqual(self.gate(approval_ref="PROD-CE-0001-20261016",
                                   environ={REF_ENV: "PROD-CE-0001-20261016"}), "approval_missing")
        self.assertEqual(self.gate(co_reference="CO-0800"), "approval_co_mismatch")
        self.assertEqual(self.gate(now=NOW + timedelta(days=8)), "approval_expired")
        self.assertEqual(self.gate(route="case_1_surface_only"), "approval_route_mismatch")
        approval.consume(self.path, REF, "CO-0797", ROUTE, "r29", NOW)
        self.assertEqual(self.gate(), "approval_consumed")

    def test_revision_drift_is_checked_against_the_current_salesforce_revision(self):
        self.assertEqual(self.gate(source_revision_now="r30"), "approval_revision_drift")
        self.assertEqual(self.gate(source_revision="r30", source_revision_now="r30"), "approval_revision_drift")
        self.assertEqual(self.gate(source_revision_now=""), "approval_revision_drift")
        self.assertEqual(self.gate(source_revision_now=None), "approval_revision_drift")


EXTRA_RECHECK = {"advanced_controls": "pass", "country_code": "pass", "spycloud_state": "pass", "form_schema": "pass"}


class Harness:
    """Fakes for every external side effect; counts calls. Approval and gate are the real code."""

    def __init__(self, state_dir, approval_path, click="submitted", readback=None, fill=None):
        self.state_dir, self.approval_path = state_dir, approval_path
        self.counts = {"fill": 0, "click": 0, "readback": 0, "write": 0}
        self.dup_result = None
        self.recheck_result = {"tenant_name": "pass", "domains": "pass", **EXTRA_RECHECK}
        self.source_result = None
        self.order = []
        self._click, self._fill, self._readback = click, fill, readback
        self.clock_state = {"now": NOW}
        self.on_fill = None
        self.fail_outcomes = ()
        self.fail_all = False
        self.at_click = {}
        self.outcomes = []

    def consumed(self):
        return approval.check(self.approval_path, REF, "CO-0797", ROUTE, "r29", NOW) == "approval_consumed"

    def fill_form(self, inputs):
        self.counts["fill"] += 1
        self.order.append("fill")
        if self.on_fill:
            self.on_fill()
        if isinstance(self._fill, Exception):
            raise self._fill
        return self._fill if self._fill is not None else {"tenant_name": "pass", "domains": "pass"}

    def click_confirm(self, inputs):
        self.counts["click"] += 1
        self.order.append("click")
        self.at_click = {"consumed": self.consumed(), "outcomes": list(self.outcomes)}
        if isinstance(self._click, Exception):
            raise self._click
        return self._click

    def read_back(self, inputs):
        self.counts["readback"] += 1
        self.order.append("readback")
        if isinstance(self._readback, Exception):
            raise self._readback
        if self._readback is not None:
            return self._readback
        return {"tenant_name": "Grupo Patio", "domains": ["patio.cl"], "account_id": "a" * 24, "account_uuid": UUID,
                "response_shape": ["id", "uuid", "access_token"], "contract_mismatches": [],
                "payload_mismatches": []}

    def source_check(self, inputs):
        self.counts["src"] = self.counts.get("src", 0) + 1
        self.order.append("src")
        return self.source_result if self.source_result is not None else (inputs.source_revision, False)

    def dup_check(self, inputs):
        self.counts["dup"] = self.counts.get("dup", 0) + 1
        self.order.append("dup")
        return self.dup_result

    def form_recheck(self, inputs):
        self.counts["recheck"] = self.counts.get("recheck", 0) + 1
        self.order.append("recheck")
        if isinstance(self.recheck_result, Exception):
            raise self.recheck_result
        return self.recheck_result

    def write_record(self, data):
        self.counts["write"] += 1
        self.order.append("write")
        if self.fail_all or data.get("outcome") in self.fail_outcomes:
            raise OSError("disk full: secret-looking text abc123")
        path = pcr.record(self.state_dir, data, NOW)
        self.outcomes.append(data["outcome"])
        return path

    def inputs(self, **over):
        return make_inputs(approval_path=self.approval_path, **over)

    def run(self, inputs=None, dry_run=True, owner_go=False, target=target_ok, check=None, **extra):
        kwargs = dict(owner_go=owner_go, _production_target_fn=target, clock=lambda: self.clock_state["now"])
        if check is not None:
            kwargs["_approval_check_fn"] = check
        if not dry_run:
            kwargs.setdefault("prior_records", [DRY_RECORD])
            kwargs.setdefault("live_duplicate_check", self.dup_check)
            kwargs.setdefault("live_source_check", self.source_check)
            kwargs.setdefault("live_form_recheck", self.form_recheck)
        kwargs.update(extra)
        return pc.run(self.inputs() if inputs is None else inputs, dry_run, self.fill_form, self.click_confirm,
                      self.read_back, self.write_record, **kwargs)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name)
        self.approval_path = self.state / "approvals.json"
        self.issue(REF)

    def issue(self, ref):
        approval.issue(self.approval_path, ref, "CO-0797", ROUTE, "r29", "milton", frozenset({"milton"}),
                       NOW - timedelta(hours=1))

    def harness(self, **kw):
        return Harness(self.state, self.approval_path, **kw)


class TestRunSignature(unittest.TestCase):
    def test_run_has_no_replaceable_gate_or_consume(self):
        params = inspect.signature(pc.run).parameters
        for name in ("gate", "consume_approval", "writeback_go"):
            self.assertNotIn(name, params)
        for name in ("_production_target_fn", "_approval_check_fn", "clock", "owner_go", "prior_records",
                     "prior_review_cleared_by_owner"):
            self.assertEqual(params[name].kind, inspect.Parameter.KEYWORD_ONLY, name)
        self.assertIn("test-only", inspect.getdoc(pc.run).lower())

    def test_gate_and_consume_keywords_are_rejected(self):
        h = Harness(Path("."), Path("unused.json"))
        for extra in ({"gate": lambda inputs: None}, {"consume_approval": lambda inputs: None},
                      {"writeback_go": True}):
            with self.assertRaises(TypeError):
                pc.run(make_inputs(), True, h.fill_form, h.click_confirm, h.read_back, h.write_record, **extra)


class TestDryRun(Base):
    def test_dry_run_never_clicks_or_consumes(self):
        h = self.harness()
        result = h.run(dry_run=True, owner_go=True)
        self.assertEqual(result.outcome, "dry_run_passed")
        self.assertEqual(h.counts, {"fill": 1, "click": 0, "readback": 0, "write": 1})
        self.assertFalse(h.consumed())

    def test_dry_run_record_has_field_names_and_pass_fail_only(self):
        h = self.harness()
        result = h.run()
        rec = pcr.load(result.record_path)
        self.assertEqual(rec["outcome"], "dry_run_passed")
        self.assertEqual(rec["approval_ref"], REF)
        self.assertEqual(rec["form_fields_checked"], {"tenant_name": "pass", "domains": "pass"})
        for name in ("tenant_name", "domains", "account_id", "account_uuid", "plan_summary"):
            self.assertNotIn(name, rec)
        self.assertIsNone(result.readback_uuid)

    def test_dry_run_form_failure_refuses(self):
        for fill in ({"tenant_name": "pass", "domains": "fail"}, {}, "bad", {"a": "pass", "b": "ok"}):
            h = self.harness(fill=fill)
            result = h.run()
            self.assertEqual((result.outcome, result.reason), ("refused", "form_verification_failed"), fill)
            self.assertEqual(h.counts["click"], 0)
            self.assertEqual(h.counts["write"], 1)

    def test_fill_exception_refuses_without_click(self):
        h = self.harness(fill=RuntimeError("boom"))
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "form_fill_failed"))
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())

    def test_non_bool_dry_run_refuses(self):
        h = self.harness()
        result = h.run(dry_run="no", owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "dry_run_flag_invalid"))
        self.assertEqual(h.counts["fill"], 0)


class TestRefusals(Base):
    def test_every_refusal_writes_a_record_and_touches_nothing(self):
        for reason, mutation in BREAKERS:
            h = self.harness()
            inputs, target, check = apply([(reason, mutation)])
            result = h.run(inputs, dry_run=False, owner_go=True, target=target, check=check)
            self.assertEqual((result.outcome, result.reason), ("refused", reason))
            self.assertEqual(h.counts, {"fill": 0, "click": 0, "readback": 0, "write": 1}, reason)
            rec = pcr.load(result.record_path)
            self.assertEqual(rec["outcome"], "refused")
            self.assertEqual(rec["refusal_reason"], reason.replace(":", "_"))
            self.assertEqual(rec["redactions"], 0, reason)
            self.assertEqual(rec["co_reference"], "CO-0797")
            self.assertFalse(h.consumed())

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_default_checks_refuse_while_locked_even_with_a_valid_unlock(self):
        for environ in ({REF_ENV: REF}, unlock_environ()):
            h = self.harness()
            result = pc.run(h.inputs(environ=environ), False, h.fill_form, h.click_confirm, h.read_back,
                            h.write_record, owner_go=True, prior_records=[DRY_RECORD])
            self.assertEqual(result.outcome, "refused")
            self.assertTrue(result.reason.startswith("production_locked:"), result.reason)
            self.assertEqual((h.counts["click"], h.counts["fill"]), (0, 0))
            self.assertFalse(h.consumed())
        self.assertEqual(result.reason, "production_locked:production_runner_not_supported")

    def test_owner_go_missing_refuses(self):
        for go in (False, None, 1, "yes", "True"):
            h = self.harness()
            result = h.run(dry_run=False, owner_go=go)
            self.assertEqual((result.outcome, result.reason), ("refused", "owner_go_missing"), go)
            self.assertEqual(h.counts["click"], 0)
            self.assertFalse(h.consumed())
            self.assertEqual(h.counts["write"], 1)

    def test_owner_go_default_is_missing(self):
        h = self.harness()
        result = pc.run(h.inputs(), False, h.fill_form, h.click_confirm, h.read_back, h.write_record,
                        _production_target_fn=target_ok, clock=lambda: NOW, prior_records=[DRY_RECORD])
        self.assertEqual(result.reason, "owner_go_missing")

    def test_consume_refusal_stops_before_click(self):
        h = self.harness()
        with mock.patch.object(pc.production_approval, "consume", return_value="approval_locked") as consume:
            result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "approval_locked"))
        self.assertEqual((consume.call_count, h.counts["click"]), (1, 0))

    def test_consume_is_the_real_one_bound_to_the_approval_file(self):
        h = self.harness()
        with mock.patch.object(pc.production_approval, "consume", return_value=None) as consume:
            h.run(dry_run=False, owner_go=True)
        consume.assert_called_once_with(self.approval_path, REF, "CO-0797", ROUTE, "r29", NOW)

    def test_consume_exception_stops_before_click(self):
        h = self.harness()
        with mock.patch.object(pc.production_approval, "consume", side_effect=OSError("disk")):
            result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "approval_consume_failed"))
        self.assertEqual(h.counts["click"], 0)

    def test_record_write_failure_does_not_hide_refusal(self):
        h = self.harness()
        h.fail_all = True
        result = h.run(target=target_locked("x_y"))
        self.assertEqual(result.outcome, "refused")
        self.assertIsNone(result.record_path)
        self.assertEqual(result.record_error, "record_write_failed")

    def test_dry_run_record_failure_is_reported_not_swallowed(self):
        h = self.harness()
        h.fail_all = True
        result = h.run()
        self.assertEqual((result.outcome, result.record_path, result.record_error),
                         ("dry_run_passed", None, "record_write_failed"))
        self.assertNotIn("abc123", repr(result))


class TestFreshClock(Base):
    def test_approval_expiring_during_fill_refuses_before_consume_and_click(self):
        h = self.harness()
        h.on_fill = lambda: h.clock_state.update(now=NOW + timedelta(days=8))
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "approval_expired"))
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())

    def test_clone_going_stale_during_fill_refuses(self):
        h = self.harness()
        h.on_fill = lambda: h.clock_state.update(now=NOW + timedelta(hours=6))
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "clone_stale"))
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())

    def test_consume_uses_the_fresh_time(self):
        h = self.harness()
        h.on_fill = lambda: h.clock_state.update(now=NOW + timedelta(minutes=5))
        self.assertEqual(h.run(dry_run=False, owner_go=True).outcome, "readback_verified")
        stored = json.loads(self.approval_path.read_text(encoding="utf-8"))
        self.assertEqual(stored[REF]["consumed_at"], "2026-10-16T12:05:00Z")

    def test_clock_failure_or_naive_clock_refuses(self):
        def boom():
            raise RuntimeError("clock")
        for clock in (boom, lambda: datetime(2026, 10, 16, 12, 0, 0), lambda: "now"):
            h = self.harness()
            result = h.run(dry_run=False, owner_go=True, clock=clock)
            self.assertEqual(result.outcome, "refused")
            self.assertIn(result.reason, ("clock_unavailable", "approval_expired", "clone_stale"))
            self.assertEqual(h.counts["click"], 0)
            self.assertFalse(h.consumed())

    def test_default_clock_is_the_real_utc_time(self):
        h = self.harness()
        result = pc.run(h.inputs(), False, h.fill_form, h.click_confirm, h.read_back, h.write_record,
                        owner_go=True, _production_target_fn=target_ok, prior_records=[DRY_RECORD],
                        live_duplicate_check=h.dup_check,
                        live_source_check=h.source_check, live_form_recheck=h.form_recheck)
        # The fixtures are dated 2026-10-16 12:00 UTC; with the real clock a click only happens if the fresh gate
        # passed, and then the approval was consumed first.
        self.assertEqual(h.counts["click"], 1 if h.consumed() else 0)
        self.assertIn(result.outcome, ("refused", "readback_verified"))


class TestPriorRecords(Base):
    def test_live_run_needs_no_prior_dry_run(self):
        # Owner decision 2026-10-23: the dry run is not a gate of the live create any more.
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True, prior_records=[])
        self.assertEqual(result.outcome, "readback_verified")

    def test_missing_prior_records_input_still_refuses(self):
        h = self.harness()
        result = pc.run(h.inputs(), False, h.fill_form, h.click_confirm, h.read_back, h.write_record,
                        owner_go=True, _production_target_fn=target_ok, clock=lambda: NOW)
        self.assertEqual(result.outcome, "refused")
        self.assertNotEqual(result.reason, "dry_run_required")

    def test_bad_prior_records_fail_closed(self):
        for prior in (None, "x", {"outcome": "dry_run_passed"}, [DRY_RECORD, "x"], [DRY_RECORD, None]):
            result = self.harness().run(dry_run=False, owner_go=True, prior_records=prior)
            self.assertEqual((result.outcome, result.reason), ("refused", "prior_records_invalid"), prior)

    def test_dry_run_itself_needs_no_prior_record(self):
        self.assertEqual(self.harness().run(dry_run=True, prior_records=[]).outcome, "dry_run_passed")

    def test_prior_uncertain_or_unverified_attempt_needs_owner_review(self):
        for outcome in ("uncertain_submit", "created_unverified", "attempt_started", "readback_verified"):
            prior = [DRY_RECORD, {**DRY_RECORD, "outcome": outcome, "approval_ref": "PROD-CE-0001-20261016"}]
            h = self.harness()
            result = h.run(dry_run=False, owner_go=True, prior_records=prior)
            self.assertEqual((result.outcome, result.reason), ("refused", "prior_attempt_needs_review"), outcome)
            self.assertEqual((h.counts["click"], h.counts["fill"]), (0, 0))
            self.assertFalse(h.consumed())

    def test_owner_clearance_overrides_only_the_review_gate(self):
        prior = [DRY_RECORD, {**DRY_RECORD, "outcome": "uncertain_submit"}]
        for flag in (False, None, 1, "yes"):
            result = self.harness().run(dry_run=False, owner_go=True, prior_records=prior,
                                        prior_review_cleared_by_owner=flag)
            self.assertEqual(result.reason, "prior_attempt_needs_review", flag)
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True, prior_records=prior, prior_review_cleared_by_owner=True)
        self.assertEqual(result.outcome, "readback_verified")

    def test_other_co_attempts_do_not_block(self):
        prior = [DRY_RECORD, {**DRY_RECORD, "co_reference": "CO-0800", "outcome": "uncertain_submit"}]
        self.assertEqual(self.harness().run(dry_run=False, owner_go=True, prior_records=prior).outcome,
                         "readback_verified")

    def test_real_loaded_records_work_as_prior_records(self):
        self.harness().run(dry_run=True)
        loaded = [pcr.load(p) for p in pcr.list_records(self.state, "CO-0797")]
        self.assertEqual(self.harness().run(dry_run=False, owner_go=True, prior_records=loaded).outcome,
                         "readback_verified")


class TestLiveCreate(Base):
    def test_happy_path_order_and_counts(self):
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual(result.outcome, "readback_verified")
        self.assertEqual(h.counts, {"fill": 1, "click": 1, "readback": 1, "write": 2, "dup": 1, "src": 1,
                                       "recheck": 1})
        self.assertEqual(h.order, ["fill", "dup", "src", "recheck", "write", "click", "readback", "write"])
        self.assertEqual(h.at_click, {"consumed": True, "outcomes": ["attempt_started"]})
        self.assertEqual(h.outcomes, ["attempt_started", "readback_verified"])
        self.assertTrue(h.consumed())
        rec = pcr.load(result.record_path)
        self.assertEqual(rec["outcome"], "readback_verified")
        self.assertEqual(rec["tenant_name"], "Grupo Patio")
        self.assertEqual(rec["domains"], ["patio.cl"])
        self.assertEqual(rec["account_uuid"], UUID)
        self.assertEqual(rec["account_id"], "a" * 24)
        self.assertEqual(rec["salesforce_revision"], "r29")
        self.assertEqual(rec["response_shape"], ["id", "uuid", "access_token"])
        self.assertEqual(rec["redactions"], 0)
        self.assertIn("tenant_name", pcr.editable_parameters(rec))
        self.assertIsNone(result.record_error)

    def test_attempt_started_record_is_written_before_the_click(self):
        self.harness().run(dry_run=False, owner_go=True)
        loaded = [pcr.load(p) for p in pcr.list_records(self.state)]
        intents = [r for r in loaded if r["outcome"] == "attempt_started"]
        self.assertEqual(len(intents), 1)
        self.assertEqual((intents[0]["co_reference"], intents[0]["approval_ref"]), ("CO-0797", REF))

    def test_intent_write_failure_refuses_without_click_or_consume(self):
        h = self.harness()
        h.fail_outcomes = ("attempt_started",)
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "record_write_failed"))
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())
        self.assertEqual(h.outcomes, ["refused"])

    def test_final_record_failure_after_click_returns_the_real_outcome(self):
        for outcome, kw in (("readback_verified", {}), ("uncertain_submit", {"click": "uncertain"}),
                            ("created_unverified", {"readback": {"tenant_name": "x"}})):
            with self.subTest(outcome):
                self.setUp()  # fresh approval and state per case
                h = self.harness(**kw)
                h.fail_outcomes = (outcome,)
                result = h.run(dry_run=False, owner_go=True)
                self.assertEqual(result.outcome, outcome)
                self.assertIsNone(result.record_path)
                self.assertEqual(result.record_error, "record_write_failed")
                self.assertEqual(h.counts["click"], 1)

    def test_run_returns_facts_for_the_separate_writeback_step_and_never_a_plan(self):
        result = self.harness().run(dry_run=False, owner_go=True)
        self.assertEqual((result.readback_uuid, result.approval_revision, result.route), (UUID, "r29", ROUTE))
        self.assertEqual({f.name for f in dataclasses.fields(result)},
                         {"outcome", "reason", "record_path", "record_error", "readback_uuid",
                          "approval_revision", "route"})

    def test_click_once_and_never_after_uncertain(self):
        for click in ("uncertain", RuntimeError("timeout"), None, "weird", True):
            self.setUp()
            h = self.harness(click=click)
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual(result.outcome, "uncertain_submit", repr(click))
            self.assertEqual(h.counts["click"], 1, repr(click))
            self.assertTrue(h.consumed())
            self.assertEqual(h.counts["write"], 2)
            self.assertEqual(h.counts["readback"], 0)
            self.assertIsNone(result.readback_uuid)
            rec = pcr.load(result.record_path)
            self.assertEqual(rec["outcome"], "uncertain_submit")
            self.assertEqual(rec["tenant_name"], "Grupo Patio")  # expected values kept for the owner

    def test_running_again_needs_fresh_approval(self):
        self.harness().run(dry_run=False, owner_go=True)
        h2 = self.harness()
        result = h2.run(dry_run=False, owner_go=True, prior_review_cleared_by_owner=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "approval_consumed"))
        self.assertEqual(h2.counts["click"], 0)

    def test_revision_drift_refuses_before_any_click(self):
        h = self.harness()
        result = h.run(h.inputs(source_revision_now="r30"), dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "approval_revision_drift"))
        self.assertEqual((h.counts["fill"], h.counts["click"]), (0, 0))
        self.assertFalse(h.consumed())
        for missing in ("", None):
            h = self.harness()
            result = h.run(h.inputs(source_revision_now=missing), dry_run=False, owner_go=True)
            self.assertEqual(result.reason, "approval_revision_drift")
            self.assertEqual(h.counts["click"], 0)

    def test_readback_mismatches_are_created_unverified(self):
        good = {"tenant_name": "Grupo Patio", "domains": ["patio.cl"], "account_id": "a" * 24, "account_uuid": UUID}
        cases = [
            {**good, "tenant_name": "Grupo Other"},
            {**good, "domains": ["patio.cl", "extra.com"]},
            {**good, "domains": []},
            {**good, "account_uuid": "nothex"},
            {**good, "account_uuid": None},
            {**good, "account_id": ""},
            {k: v for k, v in good.items() if k != "tenant_name"},
        ]
        for rb in cases:
            self.setUp()
            h = self.harness(readback=rb)
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual(result.outcome, "created_unverified", rb)
            self.assertEqual(h.counts["click"], 1)
            self.assertIsNone(result.readback_uuid)
            self.assertEqual(pcr.load(result.record_path)["outcome"], "created_unverified")

    def test_expected_account_id_is_enforced_when_given(self):
        h = self.harness()
        self.assertEqual(h.run(h.inputs(expected_account_id="b" * 24), dry_run=False, owner_go=True).outcome,
                         "created_unverified")

    def test_expected_account_id_match_verifies(self):
        h = self.harness()
        self.assertEqual(h.run(h.inputs(expected_account_id="a" * 24), dry_run=False, owner_go=True).outcome,
                         "readback_verified")

    def test_readback_failures_are_created_unverified_with_record(self):
        for rb in (RuntimeError("waf"), "text", []):
            self.setUp()
            h = self.harness(readback=rb)
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual((result.outcome, result.reason), ("created_unverified", "readback_unavailable"))
            self.assertEqual(h.counts["click"], 1)
            self.assertEqual(pcr.load(result.record_path)["tenant_name"], "Grupo Patio")

    def test_created_unverified_records_actual_values_for_owner(self):
        rb = {"tenant_name": "Wrong Name", "domains": ["wrong.com"], "account_id": "c" * 24, "account_uuid": UUID}
        rec = pcr.load(self.harness(readback=rb).run(dry_run=False, owner_go=True).record_path)
        self.assertEqual((rec["tenant_name"], rec["domains"]), ("Wrong Name", ["wrong.com"]))
        self.assertEqual(rec["account_id"], "c" * 24)

    def test_domain_compare_is_case_and_order_insensitive(self):
        rb = {"tenant_name": "Grupo Patio", "domains": ["B.COM", " a.com "], "account_id": "a" * 24,
              "account_uuid": UUID.upper(), "contract_mismatches": [], "payload_mismatches": []}
        h = self.harness(readback=rb)
        inputs = h.inputs(expected_domains=("A.com", "b.com"))
        self.assertEqual(h.run(inputs, dry_run=False, owner_go=True).outcome, "readback_verified")


class TestCreatePayloadVerdict(Base):
    GOOD = {"tenant_name": "Grupo Patio", "domains": ["patio.cl"], "account_id": "a" * 24, "account_uuid": UUID,
            "contract_mismatches": []}

    def test_a_payload_mismatch_is_created_unverified_with_names_only(self):
        rb = {**self.GOOD, "payload_mismatches": ["scanNow", "unexpected:brandNewFlag"]}
        result = self.harness(readback=rb).run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("created_unverified", "readback_payload_mismatch"))
        record = pcr.load(result.record_path)
        self.assertEqual(record["payload_mismatches"], ["scanNow", "unexpected:brandNewFlag"])

    def test_a_readback_never_compared_to_the_create_body_cannot_verify(self):
        for rb in (dict(self.GOOD), {**self.GOOD, "payload_mismatches": None}, {**self.GOOD, "payload_mismatches": [1]}):
            self.setUp()
            result = self.harness(readback=rb).run(dry_run=False, owner_go=True)
            self.assertEqual((result.outcome, result.reason), ("created_unverified", "readback_payload_unchecked"))

    def test_both_lists_are_recorded_when_the_row_and_the_body_differ(self):
        rb = {**self.GOOD, "contract_mismatches": ["mfaRequired"], "payload_mismatches": ["scanNow"]}
        result = self.harness(readback=rb).run(dry_run=False, owner_go=True)
        self.assertEqual(result.reason, "readback_contract_mismatch")
        record = pcr.load(result.record_path)
        self.assertEqual((record["readback_mismatches"], record["payload_mismatches"]), (["mfaRequired"], ["scanNow"]))

    def test_an_empty_payload_list_verifies(self):
        rb = {**self.GOOD, "payload_mismatches": []}
        self.assertEqual(self.harness(readback=rb).run(dry_run=False, owner_go=True).outcome, "readback_verified")


class TestWritebackStep(Base):
    def verified(self):
        return self.harness().run(dry_run=False, owner_go=True)

    def test_plan_is_separate_and_needs_its_own_go(self):
        result = self.verified()
        plan = pc.writeback_plan_after_verified(result, dict(CO_ROW), "r29", False)
        self.assertEqual((plan.status, plan.reason), ("blocked", "explicit_go_missing"))
        self.assertEqual(plan.field, "Account_UUID__c")
        plan = pc.writeback_plan_after_verified(result, dict(CO_ROW), "r29", True)
        self.assertEqual((plan.status, plan.uuid), ("ready_to_write", UUID))
        argv = wb.update_argv(plan, "a0B5g00000AbCdEFGH", "alias")
        self.assertEqual(argv[0:3], ["sf", "data", "update"])

    def test_revision_change_prior_value_and_unreadable_row_block(self):
        result = self.verified()
        self.assertEqual(pc.writeback_plan_after_verified(result, dict(CO_ROW), "r30", True).reason,
                         "revision_changed")
        self.assertEqual(pc.writeback_plan_after_verified(result, dict(CO_ROW), "", True).reason, "revision_changed")
        self.assertEqual(pc.writeback_plan_after_verified(
            result, {**CO_ROW, "Account_UUID__c": "c" * 32}, "r29", True).status, "conflict")
        self.assertEqual(pc.writeback_plan_after_verified(result, None, "r29", True).reason, "co_row_unreadable")
        row = {k: v for k, v in CO_ROW.items() if k != "Account_UUID__c"}
        self.assertEqual(pc.writeback_plan_after_verified(result, row, "r29", True).reason, "co_row_unreadable")

    def test_no_plan_unless_verified(self):
        unverified = self.harness(readback={"tenant_name": "Other", "domains": ["patio.cl"], "account_id": "a" * 24,
                                            "account_uuid": UUID}).run(dry_run=False, owner_go=True)
        self.setUp()
        uncertain = self.harness(click="uncertain").run(dry_run=False, owner_go=True)
        self.setUp()
        runs = [unverified, uncertain, self.harness().run(dry_run=True),
                self.harness().run(target=target_locked("x_y"))]
        for res in runs:
            plan = pc.writeback_plan_after_verified(res, dict(CO_ROW), "r29", True)
            self.assertEqual((plan.status, plan.reason), ("blocked", "readback_not_verified"), res.outcome)

    def test_garbage_result_is_blocked(self):
        plan = pc.writeback_plan_after_verified(None, dict(CO_ROW), "r29", True)
        self.assertEqual(plan.status, "blocked")

    def test_a_forged_result_cannot_use_a_different_route(self):
        result = dataclasses.replace(self.verified(), route="case_1_new_surface_only")
        self.assertEqual(pc.writeback_plan_after_verified(result, dict(CO_ROW), "r29", True).reason,
                         "route_not_ce_only_new")


class TestRecordsAndSecrets(Base):
    def test_record_written_on_every_outcome(self):
        runs = []
        runs.append(self.harness().run(target=target_locked("a_b")))
        runs.append(self.harness().run())
        runs.append(self.harness().run(dry_run=False, owner_go=True))
        self.setUp()
        runs.append(self.harness(click="uncertain").run(dry_run=False, owner_go=True))
        self.setUp()
        runs.append(self.harness(readback={"tenant_name": "x"}).run(dry_run=False, owner_go=True))
        self.assertEqual([r.outcome for r in runs],
                         ["refused", "dry_run_passed", "readback_verified", "uncertain_submit", "created_unverified"])
        for r in runs:
            self.assertIsNotNone(r.record_path)
            self.assertTrue(Path(r.record_path).is_file())

    def test_no_secret_in_record_even_if_adapter_returns_one(self):
        rb = {"tenant_name": "Bearer abc123token", "domains": ["patio.cl"], "account_id": "a" * 24,
              "account_uuid": UUID, "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop",
              "cookie": "sessionid=ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ", "password": "hunter2"}
        result = self.harness(readback=rb).run(dry_run=False, owner_go=True)
        text = Path(result.record_path).read_text(encoding="utf-8")
        for fragment in ("abc123", "eyJ", "ZZZZZ", "hunter2", "sessionid"):
            self.assertNotIn(fragment, text)
        self.assertEqual(result.outcome, "created_unverified")

    def test_unsafe_reason_text_never_reaches_record_unredacted(self):
        result = self.harness().run(check=lambda *a: "Bearer abc123")
        text = Path(result.record_path).read_text(encoding="utf-8")
        self.assertNotIn("abc123", text)

    def test_result_is_immutable(self):
        result = self.harness().run()
        self.assertTrue(dataclasses.is_dataclass(result))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.outcome = "readback_verified"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            make_inputs().route = "x"

    def test_record_is_json_with_no_unexpected_keys(self):
        result = self.harness().run(dry_run=False, owner_go=True)
        raw = json.loads(Path(result.record_path).read_text(encoding="utf-8"))
        self.assertLessEqual(set(raw), pcr._ALLOWED)


class TestSourceBoundary(unittest.TestCase):
    def setUp(self):
        self.path = Path(pc.__file__)
        self.text = self.path.read_text(encoding="utf-8")

    def test_no_tools_or_network_or_process_imports(self):
        tree = ast.parse(self.text)
        forbidden = {"subprocess", "socket", "urllib", "requests", "http", "httpx", "asyncio", "ssl", "ftplib",
                     "smtplib", "playwright", "selenium", "tools", "os", "shutil", "webbrowser", "multiprocessing"}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [("." * node.level) + (node.module or "")]
                if node.module is None:
                    names = [a.name for a in node.names]
            for name in names:
                self.assertNotIn(name.lstrip(".").split(".")[0], forbidden, name)

    def test_source_text_has_no_forbidden_words(self):
        for pattern in (r"\bsubprocess\b", r"\bsocket\b", r"\burllib\b", r"\brequests\b", r"\bfrom tools\b",
                        r"\bimport tools\b", r"os\.system", r"os\.popen", r"\bPopen\b", r"PRODUCTION_RUNNER_SUPPORTED\s*="):
            self.assertIsNone(re.search(pattern, self.text), pattern)

    @locked_create_build  # unlock window 2026-10-22: runs as the locked build
    def test_runner_flag_still_false(self):
        self.assertIs(session_readiness.PRODUCTION_RUNNER_SUPPORTED, False)


if __name__ == "__main__":
    unittest.main()


class TestSourceRecheck(Base):
    def test_drift_or_uuid_or_error_refuses_before_consume_and_click(self):
        cases = (("r30", False, "approval_revision_drift"), ("r29", True, "salesforce_prior_uuid_present"),
                 ("", False, "source_recheck_unavailable"), ("r29", None, "source_recheck_unavailable"))
        for revision, uuid_present, reason in cases:
            self.setUp()
            h = self.harness()
            h.source_result = (revision, uuid_present)
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual((result.outcome, result.reason), ("refused", reason), (revision, uuid_present))
            self.assertEqual(h.counts["click"], 0)
            self.assertFalse(h.consumed())
            self.assertNotIn("attempt_started", h.outcomes)

    def test_exception_and_missing_check_refuse(self):
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True, live_source_check=lambda i: 1 / 0)
        self.assertEqual(result.reason, "source_recheck_unavailable")
        self.setUp()
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True, live_source_check=None)
        self.assertEqual(result.reason, "live_source_check_missing")
        self.assertEqual((h.counts["click"], h.consumed()), (0, False))

    def test_a_fresh_clock_is_read_after_the_source_check(self):
        h = self.harness()
        times = iter([NOW, NOW + timedelta(days=30)])
        result = h.run(dry_run=False, owner_go=True, clock=lambda: next(times))
        self.assertEqual(result.outcome, "refused")
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())


class TestFormRecheck(Base):
    """Review finding 1: the verified form is compared again after the live reads, right before the intent record and consume."""

    def test_order_is_duplicate_then_source_then_form_recheck_then_click(self):
        h = self.harness()
        self.assertEqual(h.run(dry_run=False, owner_go=True).outcome, "readback_verified")
        self.assertEqual([step for step in h.order if step != "write"],
                         ["fill", "dup", "src", "recheck", "click", "readback"])

    def test_a_form_that_changed_during_the_reads_refuses_before_consume_and_click(self):
        h = self.harness()
        h.recheck_result = {"tenant_name": "pass", "domains": "fail"}
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "form_changed_during_checks"))
        self.assertEqual(h.counts["click"], 0)
        self.assertFalse(h.consumed())
        self.assertNotIn("attempt_started", h.outcomes)

    def test_unverifiable_recheck_results_refuse(self):
        for bad in ({}, None, "pass", {"tenant_name": "fail"}, {"tenant_name": True}):
            h = self.harness()
            h.recheck_result = bad
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual((result.outcome, result.reason), ("refused", "form_changed_during_checks"), bad)
            self.assertEqual((h.counts["click"], h.consumed()), (0, False))

    def test_a_recheck_that_covers_fewer_keys_than_the_fill_refuses_as_missing(self):
        h = self.harness()
        h.recheck_result = {"tenant_name": "pass", **EXTRA_RECHECK}  # "domains" was verified at the fill
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual((result.outcome, result.reason), ("refused", "form_recheck_missing"))
        self.assertEqual((h.counts["click"], h.consumed()), (0, False))
        self.assertNotIn("attempt_started", h.outcomes)

    def test_a_recheck_without_any_one_extra_item_refuses_as_missing(self):
        for dropped in EXTRA_RECHECK:
            h = self.harness()
            h.recheck_result = {"tenant_name": "pass", "domains": "pass",
                                **{k: v for k, v in EXTRA_RECHECK.items() if k != dropped}}
            result = h.run(dry_run=False, owner_go=True)
            self.assertEqual((result.outcome, result.reason), ("refused", "form_recheck_missing"), dropped)
            self.assertEqual((h.counts["click"], h.consumed()), (0, False))

    def test_a_recheck_that_is_a_superset_of_the_fill_passes(self):
        h = self.harness()
        h.recheck_result = {"tenant_name": "pass", "domains": "pass", "something_new": "pass", **EXTRA_RECHECK}
        self.assertEqual(h.run(dry_run=False, owner_go=True).outcome, "readback_verified")

    def test_a_recheck_that_raises_refuses(self):
        h = self.harness()
        h.recheck_result = RuntimeError("detached")
        result = h.run(dry_run=False, owner_go=True)
        self.assertEqual(result.reason, "form_changed_during_checks")
        self.assertEqual((h.counts["click"], h.consumed()), (0, False))

    def test_a_missing_recheck_refuses_a_live_create(self):
        h = self.harness()
        result = h.run(dry_run=False, owner_go=True, live_form_recheck=None)
        self.assertEqual((result.outcome, result.reason), ("refused", "form_recheck_missing"))
        self.assertEqual((h.counts["click"], h.consumed()), (0, False))

    def test_the_recheck_is_not_used_by_a_dry_run(self):
        h = self.harness()
        self.assertEqual(h.run(dry_run=True).outcome, "dry_run_passed")
        self.assertEqual(h.counts.get("recheck", 0), 0)

    def test_the_new_reasons_are_known_and_kept_in_the_record(self):
        from integration.onboarding import production_create_live as live
        for code in ("form_changed_during_checks", "form_recheck_missing"):
            self.assertEqual(live.live_known_reason(code), code)
