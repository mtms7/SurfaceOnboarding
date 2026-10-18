"""Orchestrator for the first production CE-only create (docs/42 sections 3 and 6, docs/45 D1-D6).

Nothing here opens a connection, starts a process or reads a file: every external side effect is an injected callable
(``fill_form``, ``click_confirm``, ``read_back``, ``write_record``), so a real browser adapter can be attached later
without this module changing. The only things it does itself are the real gate (``evaluate_gate``), the real approval
consume (``production_approval.consume`` on the input's approval file) and one fresh clock reading. It never changes
``PRODUCTION_RUNNER_SUPPORTED``: while that flag is False the production target check refuses every run.

Flow: gate (first refusal wins) -> form fill and verify -> [dry run stops here, Confirm never clicked, approval never
consumed] -> live only: explicit ``owner_go``, a prior dry run for the same CO and approval, no unreviewed uncertain
attempt -> fresh clock, whole gate again -> ``attempt_started`` record (failure refuses, nothing clicked) -> consume the
approval once -> click Confirm exactly once (never retried; any doubt is ``uncertain_submit``) -> read back and compare
-> ``readback_verified`` only on an exact match. A redacted record (production_create_record) is written on every path;
a failed record write is reported in ``record_error``, never hidden. The Salesforce UUID write-back plan is a separate
step (``writeback_plan_after_verified``) with its own explicit go (decision D3); it only returns a plan and never runs it.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import production_approval, salesforce_uuid_writeback
from .run_target import ProductionLocked, production_dry_run_target, production_target

CLONE_MAX_AGE = timedelta(hours=6)
SUBMITTED = "submitted"
UNCERTAIN = "uncertain"
# ``click_confirm`` returns this when it provably sent nothing (control missing, disabled or covered). The approval is
# already consumed by then, so the attempt stays blocked for the owner's review (``attempt_started`` is on record).
NOT_CLICKED = "not_clicked"

OUTCOME_REFUSED = "refused"
OUTCOME_DRY_RUN = "dry_run_passed"
OUTCOME_CREATED_UNVERIFIED = "created_unverified"
OUTCOME_VERIFIED = "readback_verified"
OUTCOME_UNCERTAIN = "uncertain_submit"
OUTCOME_ATTEMPT_STARTED = "attempt_started"
OUTCOME_OWNER_CLEARED = "owner_review_cleared"  # recorded by the owner (tools/clear_production_attempt.py)
RECORD_ERROR = "record_write_failed"
SESSION_REF_ENV = "SURFACE_ONBOARDING_PRODUCTION_APPROVAL_REF"
# Exactly "1" in the run's environment enables the underscore seams of run() (tests only).
TEST_HOOKS_ENV = "SURFACE_ONBOARDING_TEST_HOOKS"

_REASON = re.compile(r"[a-z][a-z0-9_]{2,80}")
_DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")


@dataclass(frozen=True)
class CreateInputs:
    """Everything the gate and the create need, gathered by the caller from already-verified sources."""
    co_reference: str
    route: str
    approval_ref: str
    approval_path: Path
    source_revision: str            # Salesforce revision recorded in the approval
    source_revision_now: str        # CURRENT Salesforce revision, re-read just before the run; the gate compares it
    now: datetime
    environ: Mapping[str, str]
    sf_email_domains: Any           # tuple/list of the valid email domains found in Salesforce
    sf_source_consistent: bool
    prior_account_uuid: str | None
    clone_collected_at: datetime | None
    clone_matches: bool
    duplicate_found: bool           # same name or domain already in production
    live_paid_tenant_count: int     # live paid tenants with the exact name
    timing_allowed: bool            # existing onboarding_allowed_now rule
    csm_request_recorded: bool
    sessions_ready: bool            # auth/WAF probe ready
    schema_ok: bool                 # form/response schema matches the Dev contract (D4 probe)
    expected_tenant_name: str
    expected_domains: Any
    expected_account_id: str = ""   # optional: compared when non-empty
    plan_summary: str = ""
    # A dry run learns the session and duplicate facts in the browser before the gate runs. When ``sessions_ready`` or
    # ``duplicate_found`` fails, this more specific reason code (``^[a-z][a-z0-9_]{2,80}$``) is returned instead of the
    # generic one; empty keeps the generic code. It never opens a gate.
    precheck_reason: str = ""


class FormRefusal(Exception):
    """Raised by ``fill_form`` to refuse with a specific reason code (e.g. ``unexpected_schema``) and, optionally, the
    field names checked so far (name -> "pass"/"fail"). Anything else raised by ``fill_form`` is ``form_fill_failed``."""

    def __init__(self, reason: str, fields: Mapping[str, str] | None = None) -> None:
        reason = reason if isinstance(reason, str) and _REASON.fullmatch(reason) else "form_fill_failed"
        super().__init__(reason)
        self.reason = reason
        self.fields = dict(fields) if isinstance(fields, Mapping) else None


@dataclass(frozen=True)
class CreateResult:
    outcome: str
    reason: str
    record_path: Path | None
    record_error: str | None = None         # "record_write_failed" when the record could not be written
    readback_uuid: str | None = None        # only after readback_verified (input to the separate write-back step)
    approval_revision: str | None = None    # Salesforce revision bound in the approval
    route: str | None = None


def _aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _domain_problem(domains: Any) -> bool:
    if not isinstance(domains, (list, tuple)) or len(domains) != 1:
        return True
    domain = domains[0]
    return not (isinstance(domain, str) and _DOMAIN.fullmatch(domain.strip().lower()))


def _specific(inputs: CreateInputs, generic: str) -> str:
    reason = inputs.precheck_reason
    return reason if isinstance(reason, str) and _REASON.fullmatch(reason) else generic


def _clone_problem(inputs: CreateInputs) -> str | None:
    collected = inputs.clone_collected_at
    if collected is None:
        return "clone_missing"
    if not _aware(collected) or not _aware(inputs.now):
        return "clone_stale"
    age = inputs.now - collected
    if age < timedelta(0) or age > CLONE_MAX_AGE:
        return "clone_stale"
    if inputs.clone_matches is not True:
        return "clone_mismatch"
    return None


def evaluate_gate(inputs: CreateInputs,
                  production_target_fn: Callable[[Mapping[str, str], datetime], Any] = production_target,
                  approval_check_fn: Callable[..., str | None] = production_approval.check) -> str | None:
    """The first refusal reason, in exactly this order, or None when every gate is open. Pure: no side effect.

    1. production target: session unlock values and the runner flag (``production_locked:<reason>``);
    2. approval reference: the session unlock reference must equal ``inputs.approval_ref`` (``approval_ref_mismatch``);
    3. revision: the current Salesforce revision must be present and equal to the revision recorded in the approval
       (``approval_revision_drift``);
    4. approval check: unreadable, missing, consumed, expired, wrong CO, wrong route, or drifted revision, judged
       against the CURRENT revision;
    5. route must be CE-only new; 6. exactly one valid email domain; 7. Salesforce source consistent; 8. no prior
    Account UUID; 9. clone present, fresh and matching; 10. no duplicate in production; 11. at most one live paid
    tenant; 12. timing window or recorded CSM request; 13. sessions ready; 14. schema as expected.
    """
    try:
        production_target_fn(inputs.environ, inputs.now)
    except ProductionLocked as locked:
        return f"production_locked:{locked.reason}"
    if inputs.environ.get(SESSION_REF_ENV) != inputs.approval_ref:
        return "approval_ref_mismatch"
    current = inputs.source_revision_now
    if not isinstance(current, str) or not current or current != inputs.source_revision:
        return "approval_revision_drift"
    problem = approval_check_fn(inputs.approval_path, inputs.approval_ref, inputs.co_reference, inputs.route,
                                current, inputs.now)
    if problem is not None:
        return problem
    if inputs.route != production_approval.ALLOWED_ROUTE:
        return "route_not_ce_only_new"
    if _domain_problem(inputs.sf_email_domains):
        return "salesforce_email_domain_not_single"
    if inputs.sf_source_consistent is not True:
        return "salesforce_source_mismatch"
    if inputs.prior_account_uuid not in (None, ""):
        return "salesforce_prior_uuid_present"
    problem = _clone_problem(inputs)
    if problem is not None:
        return problem
    if inputs.duplicate_found is not False:
        return _specific(inputs, "duplicate_in_production")
    count = inputs.live_paid_tenant_count
    if isinstance(count, bool) or not isinstance(count, int) or count < 0 or count > 1:
        return "multiple_live_paid_tenants"
    if inputs.timing_allowed is not True and inputs.csm_request_recorded is not True:
        return "timing_window_not_met"
    if inputs.sessions_ready is not True:
        return _specific(inputs, "sessions_not_ready")
    if inputs.schema_ok is not True:
        return "schema_unexpected"
    return None


# --- record helpers ---------------------------------------------------------------------------------------------

def _base_record(inputs: CreateInputs, outcome: str, reason: str = "") -> dict[str, Any]:
    data: dict[str, Any] = {"co_reference": inputs.co_reference, "route": inputs.route,
                            "approval_ref": inputs.approval_ref, "outcome": outcome,
                            "salesforce_revision": inputs.source_revision}
    if reason:
        # Record reason codes are ^[a-z][a-z0-9_]{2,80}$; "production_locked:x" is stored as "production_locked_x".
        data["refusal_reason"] = reason.replace(":", "_") if isinstance(reason, str) else "invalid_reason"
    return data


def _expected_details(inputs: CreateInputs) -> dict[str, Any]:
    domains = inputs.expected_domains
    return {"tenant_name": inputs.expected_tenant_name,
            "domains": list(domains) if isinstance(domains, (list, tuple)) else [],
            "plan_summary": inputs.plan_summary}


def _write(write_record: Callable[[dict[str, Any]], Any], data: dict[str, Any]) -> tuple[Path | None, str | None]:
    """(path, None) on success, (None, "record_write_failed") on any failure. The error text is never kept."""
    try:
        path = write_record(data)
    except Exception:
        return None, RECORD_ERROR
    return (Path(path), None) if path is not None else (None, RECORD_ERROR)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _result(inputs: CreateInputs, outcome: str, reason: str, path: Path | None, error: str | None,
            readback_uuid: str | None = None) -> CreateResult:
    return CreateResult(outcome, reason, path, error, readback_uuid, _text(inputs.source_revision),
                        _text(inputs.route))


def _finish(write_record, inputs: CreateInputs, data, outcome, reason="", readback_uuid=None) -> CreateResult:
    path, error = _write(write_record, data)
    return _result(inputs, outcome, reason, path, error, readback_uuid)


def _refuse(write_record, inputs: CreateInputs, reason: str, fields: Mapping[str, str] | None = None) -> CreateResult:
    data = _base_record(inputs, OUTCOME_REFUSED, reason)
    if fields:
        data["form_fields_checked"] = dict(fields)
    return _finish(write_record, inputs, data, OUTCOME_REFUSED, reason)


def _form_verified(fields: Any) -> bool:
    return (isinstance(fields, Mapping) and len(fields) > 0
            and all(isinstance(k, str) and v == "pass" for k, v in fields.items()))


# Review fix F3 (2026-10-18): what a live re-check must cover besides every key the fill verified.
RECHECK_EXTRA_KEYS =("advanced_controls", "country_code", "spycloud_state", "form_schema")


def _recheck_complete(rechecked: Mapping[str, Any], filled: Mapping[str, Any]) -> bool:
    """The re-check result names every control the fill verified plus the extra items (it may name more, never fewer)."""
    return {k for k in filled if isinstance(k, str)} | set(RECHECK_EXTRA_KEYS) <= set(rechecked)


def _norm_domains(value: Any) -> frozenset[str] | None:
    if not isinstance(value, (list, tuple)) or not value or not all(isinstance(v, str) for v in value):
        return None
    return frozenset(v.strip().lower() for v in value)


def _compare_readback(inputs: CreateInputs, found: Any) -> str | None:
    """None when the readback matches the expected tenant, else a short reason code."""
    if not isinstance(found, Mapping):
        return "readback_unavailable"
    name = found.get("tenant_name")
    if not isinstance(name, str) or name.strip() != str(inputs.expected_tenant_name).strip():
        return "readback_name_mismatch"
    if _norm_domains(found.get("domains")) != _norm_domains(inputs.expected_domains):
        return "readback_domains_mismatch"
    account_id = found.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        return "readback_account_id_missing"
    if inputs.expected_account_id and account_id.strip() != inputs.expected_account_id.strip():
        return "readback_account_id_mismatch"
    if salesforce_uuid_writeback.normalise_uuid(found.get("account_uuid")) is None:
        return "readback_uuid_invalid"
    # docs/49 contract comparison (production_contract.compare_readback): zero mismatching field names are required, and
    # a readback that was never compared against the contract can never verify.
    mismatches = found.get("contract_mismatches")
    if not isinstance(mismatches, (list, tuple)) or not all(isinstance(m, str) for m in mismatches):
        return "readback_contract_unchecked"
    if mismatches:
        return "readback_contract_mismatch"
    # The one allowed create POST body, compared to docs/49 s.1 (field NAMES only). Never compared = never verified.
    payload = found.get("payload_mismatches")
    if not isinstance(payload, (list, tuple)) or not all(isinstance(m, str) for m in payload):
        return "readback_payload_unchecked"
    if payload:
        return "readback_payload_mismatch"
    return None


def _readback_record(inputs: CreateInputs, outcome: str, reason: str, found: Any) -> dict[str, Any]:
    data = {**_base_record(inputs, outcome, reason), **_expected_details(inputs)}
    if isinstance(found, Mapping):
        for source, target in (("tenant_name", "tenant_name"), ("account_id", "account_id"),
                               ("account_uuid", "account_uuid")):
            if source in found:
                data[target] = found[source]
        if isinstance(found.get("domains"), (list, tuple)):
            data["domains"] = list(found["domains"])
        if isinstance(found.get("response_shape"), (list, tuple)):
            data["response_shape"] = list(found["response_shape"])
        if isinstance(found.get("contract_mismatches"), (list, tuple)):
            data["readback_mismatches"] = [m for m in found["contract_mismatches"] if isinstance(m, str)]
        if isinstance(found.get("payload_mismatches"), (list, tuple)):
            data["payload_mismatches"] = [m for m in found["payload_mismatches"] if isinstance(m, str)]
    return data


# --- orchestration ----------------------------------------------------------------------------------------------

# A live attempt that left only `attempt_started` (crash after the click), an uncertain or unverified create, or an
# already verified create all need the owner's review before another create for the same CO.
_NEEDS_REVIEW = frozenset({OUTCOME_UNCERTAIN, OUTCOME_CREATED_UNVERIFIED, OUTCOME_ATTEMPT_STARTED, OUTCOME_VERIFIED})


def _prior_problem(inputs: CreateInputs, prior_records: Any, cleared_by_owner: Any) -> str | None:
    if not isinstance(prior_records, (list, tuple)) or not all(isinstance(r, Mapping) for r in prior_records):
        return "prior_records_invalid"
    same_co = [r for r in prior_records if r.get("co_reference") == inputs.co_reference]
    if not any(r.get("outcome") == OUTCOME_DRY_RUN and r.get("approval_ref") == inputs.approval_ref for r in same_co):
        return "dry_run_required"
    if cleared_by_owner is not True and any(r.get("outcome") in _NEEDS_REVIEW
                                            for r in same_co):
        return "prior_attempt_needs_review"
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def run(inputs: CreateInputs, dry_run: bool, fill_form: Callable[[CreateInputs], Any],
        click_confirm: Callable[[CreateInputs], Any], read_back: Callable[[CreateInputs], Any],
        write_record: Callable[[dict[str, Any]], Any], *, owner_go: bool = False,
        prior_records: Any = (), prior_review_cleared_by_owner: bool = False,
        clock: Callable[[], datetime] | None = None,
        live_duplicate_check: Callable[[CreateInputs], str | None] | None = None,
        live_source_check: Callable[[CreateInputs], tuple[Any, Any]] | None = None,
        live_form_recheck: Callable[[CreateInputs], Any] | None = None,
        _production_target_fn: Callable[[Mapping[str, str], datetime], Any] | None = None,
        _approval_check_fn: Callable[..., str | None] = production_approval.check) -> CreateResult:
    """Run one guarded create attempt. See the module docstring; every path writes at least one record.

    The gate and the approval consume are always the real ones and cannot be replaced. ``fill_form``,
    ``click_confirm``, ``read_back`` and ``write_record`` are the only external side effects (adapters or fakes).
    ``_production_target_fn`` and ``_approval_check_fn`` are test-only seams for the two gate inputs that read the
    process environment and the approval file; production callers never pass them. ``clock`` supplies the fresh time
    used just before the approval is consumed (default: real UTC now). A live create REQUIRES ``live_duplicate_check``:
    called after the final gate and right before the intent record, it re-checks BackOffice itself (the clone is only a
    hint) and returns a reason code or None; a missing check, an exception or any reason refuses, nothing consumed or
    clicked. A live create also REQUIRES ``live_form_recheck``: it re-reads and re-compares every verified form control
    (read only) after the live reads and right before the intent record, so a form changed meanwhile refuses with the
    approval unconsumed. ``prior_records`` are loaded
    production_create_record dicts; ``prior_review_cleared_by_owner=True`` waives only the unreviewed-attempt refusal.
    """
    # The underscore gate seams exist for tests only: without SURFACE_ONBOARDING_TEST_HOOKS == "1" in the run's
    # environment they are ignored and the real target and approval check apply.
    if not (isinstance(inputs.environ, Mapping) and inputs.environ.get(TEST_HOOKS_ENV) == "1"):
        _production_target_fn = None
        _approval_check_fn = production_approval.check
    # A dry run is gated by its own target (PRODUCTION_DRY_RUN_SUPPORTED); a live create only by production_target.
    if _production_target_fn is None:
        _production_target_fn = production_dry_run_target if dry_run is True else production_target
    reason = evaluate_gate(inputs, _production_target_fn, _approval_check_fn)
    if reason is not None:
        return _refuse(write_record, inputs, reason)
    if not isinstance(dry_run, bool):
        return _refuse(write_record, inputs, "dry_run_flag_invalid")
    if not dry_run:
        if owner_go is not True:
            return _refuse(write_record, inputs, "owner_go_missing")
        reason = _prior_problem(inputs, prior_records, prior_review_cleared_by_owner)
        if reason is not None:
            return _refuse(write_record, inputs, reason)

    try:
        fields = fill_form(inputs)
    except FormRefusal as refusal:
        return _refuse(write_record, inputs, refusal.reason, refusal.fields)
    except Exception:
        return _refuse(write_record, inputs, "form_fill_failed")
    checked = {k: v for k, v in fields.items() if isinstance(k, str)} if isinstance(fields, Mapping) else None
    if not _form_verified(fields):
        return _refuse(write_record, inputs, "form_verification_failed", checked)
    if dry_run:
        data = _base_record(inputs, OUTCOME_DRY_RUN)
        data["form_fields_checked"] = checked
        return _finish(write_record, inputs, data, OUTCOME_DRY_RUN)

    # Live create. Time passed during the form fill: read the clock again and re-run the whole gate with it.
    try:
        moment = (clock or _utc_now)()
    except Exception:
        return _refuse(write_record, inputs, "clock_unavailable", checked)
    if not _aware(moment):
        return _refuse(write_record, inputs, "clock_unavailable", checked)
    reason = evaluate_gate(dataclasses.replace(inputs, now=moment), _production_target_fn, _approval_check_fn)
    if reason is not None:
        return _refuse(write_record, inputs, reason, checked)

    # Live duplicate pre-check against BackOffice itself, as close to the click as the order allows. Fail closed.
    if live_duplicate_check is None:
        return _refuse(write_record, inputs, "live_duplicate_check_missing", checked)
    try:
        duplicate = live_duplicate_check(inputs)
    except Exception:
        duplicate = "duplicate_check_unavailable"
    if duplicate is not None:
        reason = duplicate if isinstance(duplicate, str) and _REASON.fullmatch(duplicate) else "duplicate_check_unavailable"
        return _refuse(write_record, inputs, reason, checked)

    # Salesforce re-read immediately before the consume and the click: (current revision, Account UUID present). A live
    # create REQUIRES it; drift, a non-empty UUID, an error or a missing check refuses before anything is consumed.
    if live_source_check is None:
        return _refuse(write_record, inputs, "live_source_check_missing", checked)
    try:
        fresh_revision, uuid_present = live_source_check(inputs)
    except Exception:
        return _refuse(write_record, inputs, "source_recheck_unavailable", checked)
    if not isinstance(fresh_revision, str) or not fresh_revision or uuid_present is not False:
        return _refuse(write_record, inputs, "source_recheck_unavailable" if uuid_present is None or not fresh_revision
                       else "salesforce_prior_uuid_present", checked)
    try:
        moment = (clock or _utc_now)()  # a fresh clock for the judgement that precedes the consume
    except Exception:
        return _refuse(write_record, inputs, "clock_unavailable", checked)
    if not _aware(moment):
        return _refuse(write_record, inputs, "clock_unavailable", checked)
    inputs = dataclasses.replace(inputs, now=moment, source_revision_now=fresh_revision)
    reason = evaluate_gate(inputs, _production_target_fn, _approval_check_fn)
    if reason is not None:
        return _refuse(write_record, inputs, reason, checked)

    # Time passed since the fill (the Salesforce re-read; the BackOffice reads ran with the form CLOSED before it opened,
    # owner decision 2026-10-18): compare every control again, right before anything is armed or consumed.
    if live_form_recheck is None:
        return _refuse(write_record, inputs, "form_recheck_missing", checked)
    try:
        rechecked = live_form_recheck(inputs)
    except Exception:
        rechecked = None
    if not _form_verified(rechecked):
        return _refuse(write_record, inputs, "form_changed_during_checks",
                       {k: v for k, v in rechecked.items() if isinstance(k, str) and isinstance(v, str)}
                       if isinstance(rechecked, Mapping) else checked)
    if not _recheck_complete(rechecked, fields):  # a smaller re-check than the fill is never a clearance
        return _refuse(write_record, inputs, "form_recheck_missing", checked)

    # Intent record first: if it cannot be written nothing is consumed or clicked.
    intent = {**_base_record(inputs, OUTCOME_ATTEMPT_STARTED), **_expected_details(inputs),
              "form_fields_checked": checked}
    if _write(write_record, intent)[1] is not None:
        return _refuse(write_record, inputs, RECORD_ERROR, checked)

    # Consume first (one approval, one create), then click exactly once.
    try:
        consumed = production_approval.consume(inputs.approval_path, inputs.approval_ref, inputs.co_reference,
                                               inputs.route, inputs.source_revision_now, moment)
    except Exception:
        return _refuse(write_record, inputs, "approval_consume_failed", checked)
    if consumed is not None:
        return _refuse(write_record, inputs, consumed if isinstance(consumed, str) else "approval_consume_failed",
                       checked)
    try:
        clicked = click_confirm(inputs)
    except Exception:
        clicked = UNCERTAIN
    if clicked == NOT_CLICKED:
        return _refuse(write_record, inputs, "confirm_not_clicked", checked)
    if clicked != SUBMITTED:
        data = {**_base_record(inputs, OUTCOME_UNCERTAIN, "uncertain_submit"), **_expected_details(inputs),
                "form_fields_checked": checked}
        return _finish(write_record, inputs, data, OUTCOME_UNCERTAIN, "uncertain_submit")

    try:
        found = read_back(inputs)
    except Exception:
        found = None
    # A readback of None (no row, session or reply problem, or a post-click popup) is recorded as ``created_unverified`` with
    # reason ``readback_unavailable``; production_create_ui.ui_status shows it as create_outcome_unknown and blocks the CO.
    problem = _compare_readback(inputs, found)
    if problem is not None:
        data = _readback_record(inputs, OUTCOME_CREATED_UNVERIFIED, problem, found)
        data["form_fields_checked"] = checked
        return _finish(write_record, inputs, data, OUTCOME_CREATED_UNVERIFIED, problem)

    data = _readback_record(inputs, OUTCOME_VERIFIED, "", found)
    data["form_fields_checked"] = checked
    return _finish(write_record, inputs, data, OUTCOME_VERIFIED, "",
                   salesforce_uuid_writeback.normalise_uuid(found.get("account_uuid")))


def writeback_plan_after_verified(result: Any, co_row_now: Any, source_revision_now: Any,
                                  writeback_go: Any) -> salesforce_uuid_writeback.WritebackPlan:
    """Separate step (decision D3): the Account UUID write-back plan for a verified create. Never executes anything.

    Uses the readback UUID and the revision bound in the approval; ``co_row_now`` and ``source_revision_now`` are
    re-read by the caller just before. Anything but ``readback_verified`` is blocked.
    """
    if not isinstance(result, CreateResult):
        return salesforce_uuid_writeback.WritebackPlan("blocked", "result_invalid")
    return salesforce_uuid_writeback.plan(
        "production", result.route, co_row_now, result.readback_uuid, result.outcome == OUTCOME_VERIFIED,
        source_revision_now, result.approval_revision, writeback_go)
