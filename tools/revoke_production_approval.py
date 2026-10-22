"""Owner-run tool: retire ONE unused production approval (owner request 2026-10-22).

Example: CO-0800 was created by hand, so its approval was never consumed and still blocks the next CO (only one approval may be live).
Interactive only (a typed ``REVOKE <approval ref>``), desktop only, the approval's issuer must be an allowed issuer. It sets
``superseded_at`` on that one approval (``production_approval.revoke``) and nothing else: it unlocks nothing, creates nothing and never
touches Salesforce or BackOffice. ``--dry-run`` shows the summary and writes nothing. Output: fixed words, the CO, the route and the
approval reference only (no tenant name, no address).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import production_approval  # noqa: E402
from tools.issue_production_approval import (  # noqa: E402
    RUNTIME_ENV, Refuse, default_state_path, issuer_allow_list)

_STAMP = "%Y-%m-%dT%H:%M:%SZ"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # never echo the offending value
        raise Refuse("invalid_arguments")


def _emit(out: Any, payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), file=out)


def _run(argv: Sequence[str], now_fn: Callable[[], datetime], environ: Mapping[str, str], input_fn: Callable[[str], str],
         isatty_fn: Callable[[], bool], state_path: Path, out: Any) -> int:
    parser = _Parser(prog="revoke_production_approval.py", add_help=False)
    parser.add_argument("--approval-ref", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv))
    ref = args.approval_ref
    if not production_approval._valid_ref(ref):
        raise Refuse("invalid_approval_ref")
    now = now_fn().astimezone(timezone.utc).replace(microsecond=0)
    if str(environ.get(RUNTIME_ENV, "desktop")).strip().casefold() == "vm":
        raise Refuse("desktop_only")
    allowed = issuer_allow_list(environ)
    record = production_approval.record_for(state_path, ref)
    problem = production_approval.revocable_problem(record, now)
    if problem is not None:
        raise Refuse(problem)
    issuer = str(record["issued_by"]).strip().casefold()  # type: ignore[index]
    if issuer not in allowed:
        raise Refuse("issuer_not_allowed")
    print(f"CO: {record['co_reference']}", file=out)  # type: ignore[index]
    print(f"Route: {record['route']}", file=out)  # type: ignore[index]
    print(f"Approval ref: {ref}", file=out)
    print(f"Expires (UTC): {record['expires_at']}", file=out)  # type: ignore[index]
    print("This retires ONE unused approval so the next CO can be approved; it unlocks and creates nothing.", file=out)
    if args.dry_run:
        print("Dry run: nothing was written.", file=out)
        return 0
    if not isatty_fn():
        raise Refuse("not_interactive")
    if input_fn(f"Type REVOKE {ref} to retire this approval: ") != f"REVOKE {ref}":
        raise Refuse("confirmation_phrase_mismatch")
    try:
        production_approval.revoke(state_path, ref, now)
    except production_approval.ApprovalRefused as refused:
        raise Refuse(refused.reason) from None
    _emit(out, {"result": "revoked", "approval_ref": ref})
    return 0


def main(argv: Sequence[str] | None = None, *, now_fn: Callable[[], datetime] | None = None,
         environ: Mapping[str, str] | None = None, input_fn: Callable[[str], str] | None = None,
         isatty_fn: Callable[[], bool] | None = None, state_path: Path | None = None) -> int:
    out = sys.stdout
    try:
        return _run(sys.argv[1:] if argv is None else argv, now_fn or (lambda: datetime.now(timezone.utc)),
                    os.environ if environ is None else environ, input_fn or input, isatty_fn or sys.stdin.isatty,
                    state_path or default_state_path(), out)
    except Refuse as refused:
        _emit(out, {"result": "refused", "reason": refused.reason})
    except BaseException as error:  # noqa: BLE001 - fail closed; never print values or a traceback
        if isinstance(error, (KeyboardInterrupt, EOFError)):
            _emit(out, {"result": "refused", "reason": "confirmation_phrase_mismatch"})
        else:
            _emit(out, {"result": "refused", "reason": "unexpected_error"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
