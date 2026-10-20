"""Owner-run command: issue the ONE-CO production create approval (docs/45 D1, docs/42 section 6.2 step 3).

    python tools/issue_production_approval.py --co CO-0797 [--lifetime-days 7] [--approval-ref REF]
                                              [--issuer EMAIL] [--dry-run] [--status]

It reads the CO once through the dashboard's fixed read-only Salesforce helper (no new query), refuses unless the CO is a
CE-only New onboarding that is Approved, has exactly one valid email domain and no Account UUID, shows a plain summary,
and (only on an interactive terminal, after the typed phrase `ISSUE <CO>`) records the approval through
``production_approval.issue`` in the same state file the dashboard approvals page reads. Issuing an approval does not
unlock anything: it never reads or changes either production lock flag, writes nothing to
Salesforce, starts no run and prints no secret. Every refusal is one JSON line and exit code 2.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import production_approval  # noqa: E402
from integration.onboarding import production_views  # noqa: E402
from integration.onboarding import session_readiness as readiness  # noqa: E402
from integration.onboarding.state_paths import state_file  # noqa: E402

STATE_FILE_NAME = production_approval.STATE_FILE_NAME
CE_PRODUCT = "Credential Exposure"
CE_TYPE = "New Product Onboarding"
CE_ENGINE = production_approval.ALLOWED_ROUTE
CASE3_PRODUCT = "Surface & Credential Exposure"
CASE3_TYPE = "New Product Onboarding"
CASE3_ENGINE = production_approval.CASE3_ROUTE
APPROVED = "Approved"
ISSUERS_ENV = "SURFACE_ONBOARDING_APPROVAL_ISSUERS"
ALLOWED_USERS_ENV = "SURFACE_DASHBOARD_ALLOWED_USERS"  # fallback only when it holds exactly one address
RUNTIME_ENV = "SURFACE_ONBOARDING_RUNTIME"
EXPECTED_ORG_ENV = "SURFACE_SF_EXPECTED_ORG_ID"
CO_PATTERN = re.compile(r"CO-[0-9]{4,10}")
DOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
DEFAULT_LIFETIME_DAYS = 7
MAX_LIFETIME_DAYS = int(readiness.MAX_APPROVAL_LIFETIME.total_seconds() // 86400)
SUMMARY_SENTENCE = "This approves ONE production create for this CO only; it does not unlock anything."
_STAMP = "%Y-%m-%dT%H:%M:%SZ"

Reader = Callable[[str], "Mapping[str, Any] | None"]
OrgProbe = Callable[[], "str | None"]


class Refuse(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # never echo the offending value
        raise Refuse("invalid_arguments")


def default_state_path() -> Path:
    return state_file(Path(__file__).resolve().parents[1] / "integration" / STATE_FILE_NAME)


def default_reader(co: str) -> Mapping[str, Any]:
    """The dashboard's fixed read-only CO detail read (one `sf data query`, no new query, nothing written)."""
    from tools.serve_attended_open_onboardings_dashboard import detail_row
    return detail_row(co)


def default_org_probe() -> str | None:
    """The org Id of the pinned alias through the dashboard's existing read helper and org query (output not printed)."""
    from tools.serve_attended_open_onboardings_dashboard import SALESFORCE_ORG_QUERY, sf_json
    response = sf_json(["data", "query", "--query", SALESFORCE_ORG_QUERY, "--json"])
    records = response["result"]["records"]  # type: ignore[index]
    return records[0].get("Id") if isinstance(records, list) and len(records) == 1 else None


def org_problem(environ: Mapping[str, str], probe: OrgProbe, now: datetime) -> str | None:
    """None only when the pinned alias is the org named by SURFACE_SF_EXPECTED_ORG_ID (existing classification)."""
    try:
        org_id = probe()
        status = readiness.classify_salesforce_probe(0, org_id, environ.get(EXPECTED_ORG_ENV), now)
    except Exception:  # noqa: BLE001
        return "salesforce_org_not_pinned"
    return None if status.state is readiness.SessionState.READY else "salesforce_org_not_pinned"


def issuer_allow_list(environ: Mapping[str, str]) -> frozenset[str]:
    """Explicit issuer list, else the dashboard operator list only when it holds exactly one address (D1: owner only)."""
    explicit = readiness.allowed_operators(environ.get(ISSUERS_ENV))
    if explicit:
        return explicit
    fallback = readiness.allowed_operators(environ.get(ALLOWED_USERS_ENV))
    if len(fallback) != 1:
        raise Refuse("issuer_list_ambiguous")
    return fallback


def default_approval_ref(co: str, now: datetime, route: str = CE_ENGINE) -> str:
    prefix = "PROD-C3-" if route == CASE3_ENGINE else "PROD-CE-"
    return prefix + co.removeprefix("CO-") + "-" + now.astimezone(timezone.utc).strftime("%Y%m%d")


def route_for_row(row: Mapping[str, Any] | None) -> str | None:
    """The approvable route of a CO row (CE-only New or Case 3), else None."""
    if not isinstance(row, Mapping):
        return None
    pair = (row.get("Onboarding_Product__c"), row.get("Onboarding_Type__c"))
    if pair == (CE_PRODUCT, CE_TYPE):
        return CE_ENGINE
    if pair == (CASE3_PRODUCT, CASE3_TYPE):
        return CASE3_ENGINE
    return None


def _one_domain(raw: Any) -> bool:
    if not isinstance(raw, str):
        return False
    items = [item.removeprefix("@") for item in re.split(r"[,;\s]+", raw.strip()) if item]
    return len(items) == 1 and DOMAIN.fullmatch(items[0].casefold()) is not None


def eligibility_problem(row: Mapping[str, Any] | None, co: str) -> str | None:
    """None when the row is the CE-only New route or Case 3 (Surface & Credential Exposure New), Approved, one valid email
    domain (Case 3 also a valid main domain), no UUID (Case 3 also no Surface Account ID), with a revision."""
    if not isinstance(row, Mapping) or row.get("Name") != co:
        return "co_not_found"
    route = route_for_row(row)
    if route is None:
        return "route_not_ce_only"
    if row.get("Onboarding_Approval_Status__c") != APPROVED:
        return "approval_status_not_approved"
    if route == CASE3_ENGINE and not _one_domain(row.get("Main_Domain__c")):
        return "main_domain_missing"
    if not _one_domain(row.get("Email_Domains__c")):
        return "email_domain_not_single"
    uuid = row.get("Account_UUID__c")
    if uuid is not None and (not isinstance(uuid, str) or uuid.strip()):
        return "account_uuid_present"
    surface_id = row.get("Surface_Account_ID__c")
    if route == CASE3_ENGINE and surface_id is not None and (not isinstance(surface_id, str) or surface_id.strip()):
        return "surface_account_id_present"
    revision = row.get("LastModifiedDate")
    if not isinstance(revision, str) or not revision.strip():
        return "source_revision_missing"
    return None


def build_approval(**kwargs: Any) -> dict[str, Any]:
    return production_approval.issue(**kwargs)


def _emit(out: Any, payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), file=out)


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = _Parser(prog="issue_production_approval.py", add_help=False)
    parser.add_argument("--co", required=True)
    parser.add_argument("--lifetime-days", type=int, default=DEFAULT_LIFETIME_DAYS)
    parser.add_argument("--approval-ref")
    parser.add_argument("--issuer")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    return parser.parse_args(list(argv))


def _status(path: Path, co: str, now: datetime, out: Any) -> int:
    listing = production_views.list_approvals(path, now)
    if listing["state"] != "ok":
        raise Refuse(str(listing["reason"] or "approval_unreadable"))
    mine = [item for item in listing["items"] if item["co_reference"] == co]
    if not mine:
        print(f"{co}: none (no approval recorded)", file=out)
    for item in mine:
        print(f"{co}: {item['state']}  ref={item['approval_ref']}  expires={item['expires_at']}", file=out)
    return 0


def _run(argv: Sequence[str], reader: Reader, now_fn: Callable[[], datetime], environ: Mapping[str, str],
         input_fn: Callable[[str], str], isatty_fn: Callable[[], bool], state_path: Path, out: Any,
         org_probe: OrgProbe) -> int:
    args = _parse_args(argv)
    co = args.co
    if not CO_PATTERN.fullmatch(co):
        raise Refuse("invalid_co_reference")
    now = now_fn().astimezone(timezone.utc).replace(microsecond=0)
    if args.status:
        return _status(state_path, co, now, out)
    if str(environ.get(RUNTIME_ENV, "desktop")).strip().casefold() == "vm":
        raise Refuse("desktop_only")
    if not 1 <= args.lifetime_days <= MAX_LIFETIME_DAYS:
        raise Refuse("invalid_lifetime")
    if args.approval_ref is not None and not production_approval._valid_ref(args.approval_ref):
        raise Refuse("invalid_approval_ref")
    allowed = issuer_allow_list(environ)
    if args.issuer is not None:
        issuer = args.issuer.strip().casefold()
    elif len(allowed) == 1:
        issuer = next(iter(allowed))
    else:
        raise Refuse("issuer_list_ambiguous")
    if issuer not in allowed:
        raise Refuse("issuer_not_allowed")
    org = org_problem(environ, org_probe, now)
    if org is not None:
        raise Refuse(org)
    try:
        row = reader(co)
    except Exception:  # noqa: BLE001 - any read failure fails closed; nothing about it is printed
        raise Refuse("source_unreadable") from None
    problem = eligibility_problem(row, co)
    if problem is not None:
        raise Refuse(problem)
    route = route_for_row(row) or CE_ENGINE
    ref = args.approval_ref or default_approval_ref(co, now, route)
    if not production_approval._valid_ref(ref):
        raise Refuse("invalid_approval_ref")
    revision = str(row["LastModifiedDate"]).strip()  # type: ignore[index]
    lifetime = timedelta(days=args.lifetime_days)
    expires = (now + lifetime).strftime(_STAMP)
    print(f"CO: {co}", file=out)
    print(f"Route: {route}", file=out)
    print(f"Source revision: {revision}", file=out)
    print(f"Issuer: {issuer}", file=out)
    print(f"Approval ref: {ref}", file=out)
    print(f"Issued (UTC): {now.strftime(_STAMP)}", file=out)
    print(f"Expires (UTC): {expires}", file=out)
    print(f"State file: {state_path.name}", file=out)
    print(SUMMARY_SENTENCE, file=out)
    if args.dry_run:
        print("Dry run: nothing was written.", file=out)
        return 0
    if not isatty_fn():
        raise Refuse("not_interactive")
    if input_fn(f"Type ISSUE {co} to record this approval: ") != f"ISSUE {co}":
        raise Refuse("confirmation_phrase_mismatch")
    try:
        record = build_approval(path=state_path, approval_ref=ref, co_reference=co, route=route,
                                source_revision=revision, issued_by=issuer, allowed_issuers=allowed, now=now,
                                lifetime=lifetime)
    except production_approval.ApprovalRefused as refused:
        raise Refuse(refused.reason) from None
    _emit(out, {"result": "issued", "approval_ref": record["approval_ref"], "expires_at": record["expires_at"]})
    return 0


def main(argv: Sequence[str] | None = None, *, reader: Reader | None = None,
         now_fn: Callable[[], datetime] | None = None, environ: Mapping[str, str] | None = None,
         input_fn: Callable[[str], str] | None = None, isatty_fn: Callable[[], bool] | None = None,
         state_path: Path | None = None, org_probe: OrgProbe | None = None) -> int:
    out = sys.stdout
    try:
        return _run(sys.argv[1:] if argv is None else argv, reader or default_reader,
                    now_fn or (lambda: datetime.now(timezone.utc)), os.environ if environ is None else environ,
                    input_fn or input, isatty_fn or sys.stdin.isatty, state_path or default_state_path(), out,
                    org_probe or default_org_probe)
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
