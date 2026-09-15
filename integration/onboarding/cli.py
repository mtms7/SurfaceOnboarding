"""Local-only CLI preparation; it has no external adapter or worker command."""

from __future__ import annotations

import argparse
from datetime import time
import json
import sys
from typing import Sequence, TextIO

from .adapters import SyncScope
from .audit_view import audit_rows
from .authorization import Role
from .config import AppConfig, PollSchedule, TargetEnvironment
from .mappings import MAPPING_REGISTRY
from .readiness import ApprovalGates, readiness_report
from .secrets import DisabledSecretProvider
from .service import LocalOnboardingService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="surface-onboarding-local")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("queue", help="print the local metadata-only queue")
    commands.add_parser("audit", help="print the safe local audit projection")
    commands.add_parser("readiness", help="print local approval and configuration gaps")

    sync = commands.add_parser("sync", help="create or coalesce a local metadata-only sync job")
    sync.add_argument("--actor", required=True)
    sync.add_argument("--role", choices=[role.value for role in Role], required=True)
    scope = sync.add_mutually_exclusive_group(required=True)
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--record-id")
    scope.add_argument("--co-number")
    return parser


def _scope_from_args(args: argparse.Namespace) -> SyncScope:
    if args.all:
        return SyncScope("all")
    if args.record_id is not None:
        return SyncScope("record_id", args.record_id)
    return SyncScope("co_number", args.co_number)


def run(argv: Sequence[str], *, service: LocalOnboardingService | None = None,
        output: TextIO | None = None) -> int:
    """Run only local queue/sync metadata actions and emit safe JSON."""
    parser = build_parser()
    args = parser.parse_args(argv)
    destination = output or sys.stdout
    local_service = service or LocalOnboardingService()

    if args.command == "queue":
        rows = [
            {
                "co_number": row.co_number,
                "engine_value": row.engine_value.value,
                "state": row.state.value,
                "reason_code": row.reason_code.value if row.reason_code else None,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in local_service.queue()
        ]
        print(json.dumps(rows, separators=(",", ":")), file=destination)
        return 0

    if args.command == "audit":
        rows = [
            {
                "event_type": row.event_type.value,
                "actor": row.actor,
                "occurred_at": row.occurred_at,
                "reason_code": row.reason_code.value if row.reason_code else None,
            }
            for row in audit_rows(local_service.audit_events())
        ]
        print(json.dumps(rows, separators=(",", ":")), file=destination)
        return 0

    if args.command == "readiness":
        report = readiness_report(
            AppConfig(TargetEnvironment.LEONARDO_DEVELOPMENT,
                      PollSchedule("UTC", (time(8), time(16)))),
            approvals=ApprovalGates(), mappings=MAPPING_REGISTRY,
            secret_provider=DisabledSecretProvider(),
        )
        rows = [{"gate": item.gate.value, "ready": item.ready, "reason": item.reason}
                for item in report.items]
        print(json.dumps({"ready": report.ready, "items": rows}, separators=(",", ":")), file=destination)
        return 0

    try:
        result = local_service.request_sync(
            _scope_from_args(args), requested_by=args.actor, role=Role(args.role)
        )
    except (PermissionError, ValueError):
        # Preserve the specific cause only in approved internal audit paths;
        # command output must not echo a future source or adapter exception.
        parser.error("sync_request_rejected")
    print(json.dumps({
        "job_id": str(result.job.job_id),
        "status": result.job.status.value,
        "coalesced": result.coalesced,
    }, separators=(",", ":")), file=destination)
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
