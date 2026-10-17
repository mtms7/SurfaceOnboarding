"""Owner-run command: record YOUR review of a production create attempt that provably sent nothing (docs/53).

    python tools/clear_production_attempt.py --co CO-0798 --approval-ref REF --revision REV --operator EMAIL   (type: CLEAR CO-0798)
    python tools/clear_production_attempt.py --co CO-0798 --uuid-write --operator EMAIL                       (type: CLEAR UUID CO-0798)

``--operator`` is required and must be the person who issued the approval (Milton only, decision D1): an approval of this CO
(for the attempt form: the named approval) must have been issued by that identity, else the command refuses.
First form: only an attempt that is an `attempt_started` record followed by the `confirm_not_clicked` refusal of the SAME
approval reference AND Salesforce revision can be cleared; an unknown, mismatched or verified create never can. It writes one
append-only `owner_review_cleared` record (operator, time, reason, approval reference, revision). A cleared attempt no longer
blocks the CO, but the consumed approval stays consumed: a NEW approval and a NEW dry run are still required. Second form:
records your review of an uncertain Salesforce UUID write (operator, time, reason) so one further write can be attempted.
Never touches BackOffice or Salesforce; interactive terminal only; prints no secret.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import production_approval, production_create, production_create_record, production_create_ui  # noqa: E402
from integration.onboarding import production_views  # noqa: E402
from integration.onboarding.state_paths import state_dir, state_file  # noqa: E402

CO = re.compile(r"CO-[0-9]{4,10}")
REASON = "owner_reviewed_confirm_not_clicked"


def _line(out: Any, payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), file=out)


def _issuer_ok(approvals: Path, co: str, ref: str | None, operator: str, now: datetime) -> bool:
    """True when the operator is the issuer of the named approval (or, with no reference, of an approval of this CO)."""
    listing = production_views.list_approvals(approvals, now)
    if listing["state"] != "ok":
        return False
    refs = [i["approval_ref"] for i in listing["items"] if i["co_reference"] == co and (ref is None or i["approval_ref"] == ref)]
    for item in refs:
        record = production_approval.record_for(approvals, item)
        if record and production_create_ui._owner_ok(operator, record):
            return True
    return False


def run(argv: Sequence[str], *, parent: Path, now: datetime, input_fn: Callable[[str], str], isatty: Callable[[], bool],
        out: Any = sys.stdout, approvals: Path | None = None) -> int:
    approvals = approvals or state_file(Path(__file__).resolve().parents[1] / "integration" / production_approval.STATE_FILE_NAME)
    parser = argparse.ArgumentParser(prog="clear_production_attempt.py", add_help=False)
    parser.add_argument("--co", required=True)
    parser.add_argument("--approval-ref")
    parser.add_argument("--revision")
    parser.add_argument("--operator")
    parser.add_argument("--uuid-write", action="store_true")
    try:
        args = parser.parse_args(list(argv))
    except SystemExit:
        _line(out, {"result": "refused", "reason": "invalid_arguments"})
        return 2
    if not CO.fullmatch(args.co) or bool(args.approval_ref) == bool(args.uuid_write) or (args.approval_ref and not args.revision):
        _line(out, {"result": "refused", "reason": "invalid_arguments"})
        return 2
    if not isinstance(args.operator, str) or not args.operator.strip():
        _line(out, {"result": "refused", "reason": "operator_required"})
        return 2
    operator = args.operator.strip()
    if not isatty():
        _line(out, {"result": "refused", "reason": "interactive_terminal_required"})
        return 2
    if not _issuer_ok(approvals, args.co, args.approval_ref, operator, now):
        _line(out, {"result": "refused", "reason": "operator_not_owner"})
        return 2
    phrase = ("CLEAR UUID " if args.uuid_write else "CLEAR ") + args.co
    if input_fn(f"Type {phrase} to record your review: ").strip() != phrase:
        _line(out, {"result": "refused", "reason": "phrase_mismatch"})
        return 2
    if args.uuid_write:
        state = production_create_ui.last_uuid_write(parent, args.co)
        if state not in ("write_uncertain", "write_started"):
            _line(out, {"result": "refused", "reason": "nothing_to_clear"})
            return 2
        try:
            production_create_ui.record_uuid_write(parent, args.co, "0" * 32, "owner_review_cleared", now, operator,
                                                   "owner_reviewed_uuid_write")
        except OSError:
            _line(out, {"result": "refused", "reason": "record_write_failed"})
            return 2
        _line(out, {"result": "recorded", "kind": "uuid_write_review"})
        return 0
    try:
        history = production_create_ui.load_co_records(parent, args.co)
    except Exception:  # noqa: BLE001 - any unreadable record fails closed
        _line(out, {"result": "refused", "reason": "records_unreadable"})
        return 2
    ref, revision = args.approval_ref, args.revision
    mine = [r for r in history if r.get("approval_ref") == ref and r.get("salesforce_revision") == revision]
    started = [r for r in mine if r.get("outcome") == production_create.OUTCOME_ATTEMPT_STARTED]
    refused = [r for r in mine if r.get("outcome") == production_create.OUTCOME_REFUSED and r.get("refusal_reason") == "confirm_not_clicked"]
    cleared = [r for r in mine if r.get("outcome") == production_create.OUTCOME_OWNER_CLEARED]
    other = [r for r in mine if r.get("outcome") in production_create._NEEDS_REVIEW
             and r.get("outcome") != production_create.OUTCOME_ATTEMPT_STARTED]
    # Every started attempt needs its own not-clicked refusal; one clear per attempt (counts, not presence).
    if not started or len(refused) < len(started) or other or len(cleared) >= len(started):
        _line(out, {"result": "refused", "reason": "attempt_not_clearable"})
        return 2
    try:
        path = production_create_record.record(parent, {
            "co_reference": args.co, "approval_ref": ref, "salesforce_revision": revision, "route": production_approval.ALLOWED_ROUTE,
            "outcome": production_create.OUTCOME_OWNER_CLEARED, "refusal_reason": REASON, "operator": operator}, now)
    except Exception:  # noqa: BLE001
        _line(out, {"result": "refused", "reason": "record_write_failed"})
        return 2
    _line(out, {"result": "recorded", "kind": "attempt_review", "record": path.name})
    return 0


def main() -> int:
    parent = state_dir() or Path(__file__).resolve().parents[1]
    return run(sys.argv[1:], parent=parent, now=datetime.now(timezone.utc), input_fn=input, isatty=sys.stdin.isatty)


if __name__ == "__main__":
    raise SystemExit(main())
