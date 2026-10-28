"""Desktop: build the signed bundle of the masked production create records that the restricted push sends to the VM.

Owner request 2026-10-27: the VM queue must show what the desktop shows ("Verified", the onboarded line, Created counts). The VM
never receives approval files; it receives only the create records the desktop dashboard itself reads, already masked by the
record writer, and merges them into its CURRENT snapshot. No Salesforce, no Redash, no browser, no key other than the existing
snapshot HMAC key. Nothing is sent here: ``tools/run_clone_push.ps1`` does the transfer.

    python tools/build_records_push_bundle.py --key-file D:/outside/repo/snapshot.key --out D:/work/records.zip

Prints counts and the output path only. Exit 0 = written, 2 = refused (short code), 64 = usage.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding import production_create_record as records  # noqa: E402
from integration.onboarding import state_paths  # noqa: E402
from integration.onboarding import vm_snapshot as vs  # noqa: E402


class BuildRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def records_parent() -> Path:
    """The folder that holds ``production-create-records``: the state folder when set, else the repository (as the dashboard)."""
    folder = state_paths.state_dir()
    return Path(folder) if folder is not None else ROOT


def build(key_file: str, out: Path, parent: Path | None = None, now: datetime | None = None) -> dict[str, int]:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        key = vs.load_key(key_file)
    except vs.SnapshotError as error:
        raise BuildRefused(error.code) from None
    parent = parent or records_parent()
    entries: dict[str, dict] = {}
    try:
        for path in records.list_records(parent):  # a corrupt record raises: nothing partial is ever sent
            entries[path.name] = records.load(path)
    except (records.ProductionCreateRecordError, OSError, ValueError, TypeError, KeyError):
        raise BuildRefused("record_unreadable") from None
    payload = json.dumps({"records": entries}, sort_keys=True).encode("utf-8")
    try:
        data = vs.build_bundle({vs.PRODUCTION_RECORDS_FILE: payload}, key, vs.RECORDS_PUSH_PRODUCER, now)
        out.write_bytes(data)
    except (vs.SnapshotError, OSError) as error:
        raise BuildRefused(getattr(error, "code", "write_failed")) from None
    return {"records": len(entries), "bytes": len(data)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the signed production-create-records bundle for the VM.")
    parser.add_argument("--key-file", required=True, help="HMAC key file (outside the repository).")
    parser.add_argument("--out", required=True, help="Where to write the zip.")
    args = parser.parse_args(argv)
    try:
        counts = build(args.key_file, Path(args.out))
    except BuildRefused as refusal:
        print("refused:" + refusal.code)
        return 2
    except Exception:  # noqa: BLE001 - never a traceback with paths or values
        print("refused:unexpected_error")
        return 2
    print(f"records={counts['records']} bytes={counts['bytes']} written {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
