"""VM: refresh the queue and the CO details from Salesforce (read-only) into a new signed snapshot (docs/43 part A).

Run by a systemd timer as the dashboard's service user with the reader's credential group. It reuses the dashboard's two
fixed reads (``queue_source_rows`` and ``detail_row``: same SOQL, same fields) through the read-only REST client in
``tools/vm_salesforce_reader.py``, keeps every other file of the current snapshot (run history and Dev
inventory published from the desktop), signs the result and ingests it locally.

    python3 tools/vm_refresh_snapshot.py --config /etc/surface-onboarding/sf-reader.json \\
        --key-file /etc/surface-onboarding/snapshot.key

    python3 tools/vm_refresh_snapshot.py --dry-run     # checks configuration and key files; no network, no write

Fail closed: any problem leaves the previous snapshot current and intact (the banner shows its age). A CO whose detail
read fails keeps its previous detail. No Salesforce write exists in this path. Output is counts and short reason codes only.
Exit 0 = refreshed (or dry run), 2 = refused, 3 = Salesforce read unavailable, 64 = usage, 75 = another refresh is running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding import vm_snapshot  # noqa: E402
from tools import vm_salesforce_reader as reader  # noqa: E402

DEFAULT_STATE_DIR = "/var/lib/surface-onboarding"
LOCK_NAME = "vm-refresh.lock"
CARRY_EXCLUDED = frozenset({vm_snapshot.QUEUE_FILE, vm_snapshot.CO_DETAILS_FILE})


class RefreshRefused(Exception):
    def __init__(self, code: str, exit_code: int = 2) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def carried_files(state_dir: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Every file of the current snapshot except queue and details (state, inventory), and its previous CO details."""
    root = vm_snapshot.snapshots_root(state_dir)
    directory = vm_snapshot.current_dir(root)
    manifest = vm_snapshot.read_current_manifest(root)
    if directory is None or manifest is None:
        return {}, {}
    files: dict[str, bytes] = {}
    previous_details: dict[str, Any] = {}
    for entry in manifest.get("files") or ():
        name = entry.get("name") if isinstance(entry, dict) else None
        if name not in vm_snapshot.ALLOWED_NAMES:
            continue
        try:
            data = (directory / name).read_bytes()
        except OSError:
            raise RefreshRefused("current_snapshot_unreadable") from None
        if name == vm_snapshot.CO_DETAILS_FILE:
            try:
                details = json.loads(data.decode("utf-8")).get("details")
            except (UnicodeError, ValueError, AttributeError):
                details = None
            if isinstance(details, dict):
                previous_details = details
        elif name not in CARRY_EXCLUDED:
            files[name] = data
    return files, previous_details


def refresh(config_path: str, key_file: str, state_dir: str, *, opener: Any | None = None,
            sign: Callable[[str, bytes], bytes] = reader.openssl_sign,
            now: datetime | None = None, dashboard: Any | None = None) -> dict[str, int]:
    try:
        key = vm_snapshot.load_key(key_file)
        config = reader.load_config(config_path)
        reader._private_file(Path(config.private_key_file), secret=True, reason="key_file_unsafe")
    except vm_snapshot.SnapshotError as error:
        raise RefreshRefused(error.code) from None
    except reader.ReaderError as error:
        raise RefreshRefused(error.reason) from None
    if dashboard is None:
        import tools.serve_attended_open_onboardings_dashboard as dashboard  # noqa: PLW0127
    if dashboard.state_paths.vm_mode():
        raise RefreshRefused("runtime_must_not_be_vm")  # VM mode serves snapshots; this process must read Salesforce live
    carried, previous_details = carried_files(state_dir)
    client = reader.RestQueryClient(config, opener, sign, clock=lambda: now or datetime.now(timezone.utc))
    original = dashboard.sf_json
    dashboard.sf_json = reader.make_sf_json(client, dashboard.ReadUnavailable)
    try:
        import tools.publish_vm_snapshot as publisher

        try:
            reads, counts = publisher.collect_dashboard_reads(sorted(previous_details))
        except publisher.PublishRefused as refusal:
            raise RefreshRefused(refusal.code, refusal.exit_code) from None
    finally:
        dashboard.sf_json = original
    details = json.loads(reads[vm_snapshot.CO_DETAILS_FILE].decode("utf-8"))["details"]
    kept = 0
    for reference, detail in previous_details.items():  # a transient failure must not drop a CO's page
        if reference not in details:
            details[reference] = detail
            kept += 1
    reads[vm_snapshot.CO_DETAILS_FILE] = json.dumps({"details": details}, sort_keys=True).encode("utf-8")
    files = {**carried, **reads}
    created = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        bundle = vm_snapshot.build_bundle(files, key, vm_snapshot.PRODUCER_VM_READER, created)
        vm_snapshot.ingest_bundle(bundle, key, state_dir)
    except vm_snapshot.SnapshotError as error:
        raise RefreshRefused(error.code) from None
    counts["co_details_kept_from_previous"] = kept
    counts["files_in_bundle"] = len(files)
    return counts


def _lock(state_dir: str) -> Any:
    import fcntl

    Path(state_dir).mkdir(parents=True, exist_ok=True)
    handle = open(Path(state_dir) / LOCK_NAME, "a+")  # noqa: SIM115 - held for the process lifetime
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RefreshRefused("refresh_already_running", 75) from None
    return handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the VM snapshot from Salesforce (read-only).")
    parser.add_argument("--config", default=os.environ.get(reader.CONFIG_ENV, ""), help="Reader configuration JSON file.")
    parser.add_argument("--key-file", default="", help="Snapshot HMAC key file (root-owned, outside the repository).")
    parser.add_argument("--state-dir", default=os.environ.get("SURFACE_ONBOARDING_STATE_DIR", "") or DEFAULT_STATE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Check the configuration and key files only.")
    args = parser.parse_args(argv)
    if not args.config or not args.key_file or not os.path.isabs(args.state_dir):
        print("usage: --config and --key-file are required and --state-dir must be absolute")
        return 64
    try:
        if args.dry_run:
            vm_snapshot.load_key(args.key_file)
            config = reader.load_config(args.config)
            reader._private_file(Path(config.private_key_file), secret=True, reason="key_file_unsafe")
            print("dry run: configuration and key files are acceptable; nothing was read or written")
            return 0
        handle = _lock(args.state_dir)
        try:
            counts = refresh(args.config, args.key_file, args.state_dir)
        finally:
            handle.close()
    except RefreshRefused as refusal:
        print("refused:" + refusal.code)
        return refusal.exit_code
    except (vm_snapshot.SnapshotError, reader.ReaderError) as error:
        print("refused:" + (getattr(error, "code", None) or getattr(error, "reason", "error")))
        return 2
    except Exception:  # noqa: BLE001 - never print a traceback that could echo a path or value
        print("refused:unexpected_error")
        return 2
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
