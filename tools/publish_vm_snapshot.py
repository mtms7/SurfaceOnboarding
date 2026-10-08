"""Desktop: build the signed snapshot zip for the read-only VM dashboard (docs/41, owner decisions 2026-10-08).

Reads local state files, the Dev inventory snapshot, and the two fixed dashboard reads that already exist
(``queue_source_rows`` and ``detail_row``: the same code paths, no new query, fields exactly QUEUE_FIELDS and
DETAIL_FIELDS). Writes ONE zip into a local folder; nothing is sent anywhere. The owner copies the zip to the VM.

    python tools/publish_vm_snapshot.py --key-file C:\\path\\outside\\repo\\snapshot.key --out-dir C:\\path\\out
    python tools/publish_vm_snapshot.py --dry-run          # lists what would be included; reads no Salesforce

Optional automatic publish after each run (default OFF; best effort, never changes a run result):
SURFACE_VM_SNAPSHOT_AUTOPUBLISH=1 with SURFACE_VM_SNAPSHOT_KEY_FILE and SURFACE_VM_SNAPSHOT_OUT_DIR.

Output is counts and the output path only: no file content, no key material. Exit 0 = published (or dry run),
2 = refused (short code printed), 3 = a required read was unavailable, 64 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding import vm_snapshot  # noqa: E402
from integration.onboarding.state_paths import state_file  # noqa: E402

KEY_FILE_ENV = "SURFACE_VM_SNAPSHOT_KEY_FILE"
OUT_DIR_ENV = "SURFACE_VM_SNAPSHOT_OUT_DIR"
AUTOPUBLISH_ENV = "SURFACE_VM_SNAPSHOT_AUTOPUBLISH"
AUTOPUBLISH_NAME = "surface-vm-snapshot-latest.zip"
PRODUCER_DEFAULT = "desktop"
_INVENTORY_FILE = re.compile(r"(?:(?:leonardo-dev|backoffice-prod|redash-prod-clone)-)?inventory-\d{8}T\d{6}Z\.json")


class PublishRefused(Exception):
    def __init__(self, code: str, exit_code: int = 2) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def collect_state_files(only: list[str] | None = None) -> dict[str, bytes]:
    """The allow-listed local state files that exist. A name that is not on the allow-list is refused, never skipped."""
    names = sorted(vm_snapshot.STATE_FILE_NAMES if not only else only)
    found: dict[str, bytes] = {}
    for name in names:
        if name not in vm_snapshot.STATE_FILE_NAMES:
            raise PublishRefused("name_not_allowed")
        path = state_file(ROOT / "integration" / name)
        if path.is_symlink():
            raise PublishRefused("state_file_is_a_link")
        if not path.is_file():
            continue
        data = path.read_bytes()
        if len(data) > vm_snapshot.MAX_FILE_BYTES:
            raise PublishRefused("file_too_large")
        found[name] = data
    return found


def collect_inventory_files() -> dict[str, bytes]:
    """The latest Dev inventory snapshot as a pair (latest pointer + the snapshot it names), or nothing.

    The production clone (Redash) is not published: it is far larger than the per-file cap.
    """
    from integration.onboarding import leonardo_inventory as inventory

    directory = inventory.env_dir(inventory.default_root(), "dev")
    try:
        latest_bytes = (directory / inventory.LATEST_FILE).read_bytes()
        name = json.loads(latest_bytes.decode("utf-8"))["file"]
        if type(name) is not str or not _INVENTORY_FILE.fullmatch(name):
            return {}
        snapshot_bytes = (directory / name).read_bytes()
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if len(snapshot_bytes) > vm_snapshot.MAX_FILE_BYTES or len(latest_bytes) > vm_snapshot.MAX_FILE_BYTES:
        return {}
    return {vm_snapshot.INVENTORY_DEV_LATEST: latest_bytes, vm_snapshot.INVENTORY_DEV_SNAPSHOT: snapshot_bytes}


def collect_dashboard_reads() -> tuple[dict[str, bytes], dict[str, int]]:
    """queue.json and co_details.json through the dashboard's existing fixed reads (Salesforce, read-only)."""
    import tools.serve_attended_open_onboardings_dashboard as dash

    try:
        rows = dash.queue_source_rows()
    except dash.ReadUnavailable:
        raise PublishRefused("queue_unavailable", 3) from None
    queue = [{field: row.get(field) for field in dash.QUEUE_FIELDS} for row in rows]
    references = {row["Name"] for row in queue if row.get("Name")}
    try:
        references |= set(dash.dev_onboarded_refs())  # onboarded COs leave the queue but keep their page
    except Exception:  # noqa: BLE001 - local evidence only; the queued COs are still published
        pass
    details: dict[str, dict[str, Any]] = {}
    missing = 0
    for reference in sorted(references):
        try:
            row = dash.detail_row(reference)
        except dash.ReadUnavailable:
            missing += 1
            continue
        details[reference] = {field: row.get(field) for field in dash.DETAIL_FIELDS}
    files = {
        vm_snapshot.QUEUE_FILE: json.dumps({"rows": queue}, sort_keys=True).encode("utf-8"),
        vm_snapshot.CO_DETAILS_FILE: json.dumps({"details": details}, sort_keys=True).encode("utf-8"),
    }
    return files, {"queue_rows": len(queue), "co_details": len(details), "co_details_unavailable": missing}


def output_path(out_dir: Path, autopublish: bool, now: datetime) -> Path:
    name = AUTOPUBLISH_NAME if autopublish else "surface-vm-snapshot-" + now.strftime("%Y%m%dT%H%M%SZ") + ".zip"
    return out_dir / name


def publish(key_file: str | os.PathLike[str], out_dir: Path, *, producer: str = PRODUCER_DEFAULT,
            only: list[str] | None = None, autopublish: bool = False) -> tuple[Path, dict[str, int]]:
    try:
        key = vm_snapshot.load_key(key_file)
        files = collect_state_files(only)
        counts = {"state_files": len(files)}
        if only is None:
            files.update(collect_inventory_files())
            counts["inventory_files"] = sum(1 for name in files if name.startswith("inventory_"))
            reads, read_counts = collect_dashboard_reads()
            files.update(reads)
            counts.update(read_counts)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        target = vm_snapshot.write_bundle(output_path(out_dir, autopublish, now), files, key, producer, now)
    except vm_snapshot.SnapshotError as error:
        raise PublishRefused(error.code) from None
    counts["files_in_bundle"] = len(files)
    return target, counts


def autopublish_after_run() -> None:
    """Best-effort publish for the optional hook; every failure is swallowed (never changes a run result)."""
    if os.environ.get(AUTOPUBLISH_ENV) != "1":
        return
    try:
        key_file, out_dir = os.environ.get(KEY_FILE_ENV, "").strip(), os.environ.get(OUT_DIR_ENV, "").strip()
        if key_file and out_dir:
            publish(key_file, Path(out_dir), autopublish=True)
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the signed VM snapshot zip (local files only; nothing is sent).")
    parser.add_argument("--key-file", default=os.environ.get(KEY_FILE_ENV, ""), help="HMAC key file (outside the repository).")
    parser.add_argument("--out-dir", default=os.environ.get(OUT_DIR_ENV, ""), help="Local folder for the zip.")
    parser.add_argument("--producer", default=PRODUCER_DEFAULT, help="Short label stored in the manifest.")
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="Publish only these state files (each must be on the allow-list); no queue or detail reads.")
    parser.add_argument("--dry-run", action="store_true", help="List what would be included; read no Salesforce, write nothing.")
    parser.add_argument("--autopublish", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.dry_run:
        try:
            state = collect_state_files(args.only)
        except PublishRefused as refusal:
            print("refused:" + refusal.code)
            return refusal.exit_code
        for name, data in sorted(state.items()):
            print(f"include {name} {len(data)} bytes")
        if args.only is None:
            for name, data in sorted(collect_inventory_files().items()):
                print(f"include {name} {len(data)} bytes")
            print(f"include {vm_snapshot.QUEUE_FILE} (live read of the open queue, not performed in a dry run)")
            print(f"include {vm_snapshot.CO_DETAILS_FILE} (live read of the queued and onboarded COs, not performed)")
        print("dry run: nothing written")
        return 0
    if not args.key_file or not args.out_dir:
        print("usage: --key-file and --out-dir are required (or the SURFACE_VM_SNAPSHOT_* variables)")
        return 64
    try:
        target, counts = publish(args.key_file, Path(args.out_dir), producer=args.producer, only=args.only,
                                 autopublish=args.autopublish)
    except PublishRefused as refusal:
        print("refused:" + refusal.code)
        return refusal.exit_code
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("written " + str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
