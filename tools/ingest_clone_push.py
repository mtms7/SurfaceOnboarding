"""VM: ingest ONE clone-only bundle pushed from the owner's PC over the restricted key (docs/51, interim path).

Run only by the root-owned wrapper (_step22a) as the dashboard's service user; the bundle arrives on STDIN, never as a path
an SSH client chooses. Stdlib only. Nothing in the bundle is ever executed: it is a zip of exactly two JSON files that
are parsed as data.

    python3 tools/ingest_clone_push.py --key-file /etc/surface-onboarding/snapshot.key < bundle.zip

Fail closed (the previous clone stays current and intact; the dashboard banner keeps showing its true age):
HMAC, sizes, names and hashes verified by ``vm_snapshot.verify_bundle``; exactly the clone pair and the producer label
``desktop-clone-push``; the clone itself re-verified by ``leonardo_inventory.load_latest`` (hash, name, environment, not
older than 6 h, not from the future); never older than anything ever accepted; never more than 10% below the LARGEST clone ever accepted (a high-water
record ``<state>/clone-push-high-water.json``, written by this program only after a verified install, so a missing or corrupt
``latest.json`` cannot reset the checks and 10% steps cannot compound); on the very first push at least FIRST_PUSH_MIN_ROWS
tenants (production is about 5,400; only an owner-run root-owned file on the VM can lower it, never the bundle).
Not detectable: a different tenant set with the same count; the production gate must keep treating a missing match as
non-clearing. Written by the inventory's own atomic writer into ``<state>/clone-inventory`` (the folder the VM Redash collector
will also use); read back and re-verified, and if the read-back fails the previous ``latest.json`` is restored and the push is
refused (never "refused" while the new data is live). The same bundle twice is a no-op.

Prints one short line (counts and codes only, never content). Exit 0 = ingested or unchanged, 2 = refused or failed,
64 = usage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import leonardo_inventory as inventory  # noqa: E402
from integration.onboarding import redash_inventory as redash  # noqa: E402
from integration.onboarding import state_paths  # noqa: E402
from integration.onboarding import vm_snapshot as vs  # noqa: E402

ENVIRONMENT = "prod-clone"
PRODUCER = vs.CLONE_PUSH_PRODUCER
CLONE_FILES = frozenset({vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT})
MAX_STDIN_BYTES = vs.MAX_BUNDLE_BYTES + 1024 * 1024
MAX_AGE = timedelta(hours=6)          # the production gate's own limit (production_create.CLONE_MAX_AGE)
FUTURE_SKEW = timedelta(minutes=5)
SHRINK_FLOOR = 0.9                    # same floor as the Redash collector
KEEP = 4
FIRST_PUSH_MIN_ROWS = 1000            # about 5,400 tenants in production; lowered only by the owner-run file below
MIN_ROWS_OVERRIDE_FILE = "/etc/surface-onboarding/clone-push-min-rows"  # root-owned, owner-created, one integer
HIGH_WATER_NAME = "clone-push-high-water.json"
STALE_STAGE_SECONDS = 3600
DEFAULT_STATE_DIR = "/var/lib/surface-onboarding"
_FOREVER = timedelta(days=3650)


class PushRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_stdin_limited(stream: BinaryIO, limit: int = MAX_STDIN_BYTES) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise PushRefused("bundle_too_large")
    if not data:
        raise PushRefused("empty_input")
    return data


def _current(root: Path, now: datetime) -> dict | None:
    """The clone currently served from ``root`` (any age), or None when there is none or it does not verify."""
    try:
        return inventory.load_latest(root, ENVIRONMENT, max_age=_FOREVER, now=now)
    except inventory.InventoryError:
        return None


def ingest(raw: bytes, key: bytes, state_dir: str | os.PathLike[str], now: datetime | None = None,
           min_rows: int | None = None) -> str:
    """Verify and install the pushed clone. Returns ``ingested ...`` or ``unchanged ...``; raises PushRefused."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    try:
        bundle = vs.verify_bundle(raw, key)
    except vs.SnapshotError as error:
        raise PushRefused(error.code) from None
    if set(bundle.files) != CLONE_FILES:
        raise PushRefused("not_a_clone_only_bundle")
    if bundle.manifest.get("producer") != PRODUCER:
        raise PushRefused("wrong_producer")
    if bundle.created_at - now > FUTURE_SKEW:
        raise PushRefused("future_bundle")
    try:
        file_name = vs._inventory_file_name(bundle.files[vs.INVENTORY_CLONE_LATEST])  # noqa: SLF001 - same package family
    except vs.SnapshotError as error:
        raise PushRefused(error.code) from None
    if not file_name.startswith("redash-prod-clone-"):
        raise PushRefused("inventory_invalid")

    root = Path(state_dir) / state_paths.CLONE_SUBDIR
    floor = FIRST_PUSH_MIN_ROWS if min_rows is None else min_rows
    _sweep_stale_stages(root)
    stage = root / f".incoming-{os.getpid()}"
    try:
        shutil.rmtree(stage, ignore_errors=True)
        directory = stage / ENVIRONMENT
        directory.mkdir(parents=True)
        (directory / inventory.LATEST_FILE).write_bytes(bundle.files[vs.INVENTORY_CLONE_LATEST])
        (directory / file_name).write_bytes(bundle.files[vs.INVENTORY_CLONE_SNAPSHOT])
        try:
            staged = inventory.load_latest(stage, ENVIRONMENT, max_age=MAX_AGE, now=now)
        except inventory.InventoryError as error:
            raise PushRefused(error.reason) from None
        payload = {name: value for name, value in staged.items() if name != "age_seconds"}
        try:  # v1 (unmarked) or v2 (query 253): a v2 row must carry only known, typed v2 keys; v1 must carry none
            redash.validate_row_schema(payload)
        except inventory.InventoryError:
            raise PushRefused("row_schema_invalid") from None
        current = _current(root, now)
        if current is not None and payload["captured_at"] == current["captured_at"] \
                and payload["rows_sha256"] == current["rows_sha256"]:
            return f"unchanged captured_at={payload['captured_at']} rows={payload['row_count']}"
        high_rows, high_at = _high_water(Path(state_dir), current)
        if high_at is None:  # the very first push: an absolute sanity floor (never taken from the bundle)
            if payload["row_count"] < floor:
                raise PushRefused("below_first_push_floor")
        else:
            if payload["captured_at"] < high_at or (current is not None and payload["captured_at"] <= current["captured_at"]):
                raise PushRefused("older_than_current")
            if high_rows > 0 and payload["row_count"] < high_rows * SHRINK_FLOOR:  # against the maximum: no compounding
                raise PushRefused("result_shrunk")
        previous_latest = _read_bytes(root / ENVIRONMENT / inventory.LATEST_FILE)
        try:
            inventory.write_snapshot(payload, root, keep=KEEP)  # snapshot file first, then latest.json, each atomic
        except (inventory.InventoryError, OSError) as error:
            _restore_latest(root, previous_latest)
            raise PushRefused(getattr(error, "reason", "store_unavailable")) from None
        readback = _readback(root, now)
        if readback is None or readback.get("rows_sha256") != payload["rows_sha256"]:
            _restore_latest(root, previous_latest)  # never leave new data live behind a refusal
            raise PushRefused("readback_failed")
        _write_high_water(Path(state_dir), max(high_rows, payload["row_count"]), max(high_at or "", payload["captured_at"]))
        return f"ingested captured_at={payload['captured_at']} rows={payload['row_count']}"
    except OSError:
        raise PushRefused("store_unavailable") from None
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _readback(root: Path, now: datetime) -> dict | None:
    return _current(root, now)


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _restore_latest(root: Path, previous: bytes | None) -> None:
    target = root / ENVIRONMENT / inventory.LATEST_FILE
    try:
        if previous is None:
            target.unlink(missing_ok=True)
            return
        temporary = target.with_name(f"{target.name}.{os.getpid()}.restore")
        temporary.write_bytes(previous)
        os.replace(temporary, target)
    except OSError:
        pass


def _sweep_stale_stages(root: Path) -> None:
    """Staging folders left by a killed run (older than 1 h) are removed."""
    try:
        for entry in root.iterdir():
            if entry.name.startswith(".incoming-") and entry.is_dir() and not entry.is_symlink() \
                    and time.time() - entry.stat().st_mtime > STALE_STAGE_SECONDS:
                shutil.rmtree(entry, ignore_errors=True)
    except OSError:
        pass


def _high_water(state_dir: Path, current: dict | None) -> tuple[int, str | None]:
    """(largest row count, newest captured_at) ever accepted. Missing = never; corrupt = refuse (never silently reset)."""
    path = state_dir / HIGH_WATER_NAME
    rows, newest = 0, None
    if path.exists():
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows, newest = record["max_row_count"], record["newest_captured_at"]
            if type(rows) is not int or rows < 0 or type(newest) is not str:
                raise ValueError("shape")
            inventory._parse_stamp(newest)  # noqa: SLF001 - same shape as every clone stamp
        except (OSError, ValueError, KeyError, TypeError, inventory.InventoryError):
            raise PushRefused("high_water_unreadable") from None
    if current is not None:  # a clone already served (for example by the VM collector) also counts
        rows = max(rows, current.get("row_count") or 0)
        newest = max(newest or "", current["captured_at"])
    return rows, newest


def _write_high_water(state_dir: Path, rows: int, newest: str) -> None:
    path = state_dir / HIGH_WATER_NAME
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"max_row_count": rows, "newest_captured_at": newest}, sort_keys=True) + "\n",
                             encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        raise PushRefused("store_unavailable") from None
    finally:
        temporary.unlink(missing_ok=True)


def _min_rows_override() -> int | None:
    try:
        text = Path(MIN_ROWS_OVERRIDE_FILE).read_text(encoding="ascii").strip()
    except OSError:
        return None
    if not text.isdigit() or not 1 <= int(text) <= 10_000_000:
        raise PushRefused("min_rows_file_invalid")
    return int(text)


def ingest_snapshot(raw: bytes, key: bytes, state_dir: str | os.PathLike[str]) -> str:
    """A full signed snapshot (Salesforce queue and CO details, local state, inventories) pushed by the PC task (owner request
    2026-10-27: the VM page showed data from 2026-10-17). Same HMAC, caps, names and no-rollback rules as the manual
    ``ingest_vm_snapshot.py``; only the producer label ``desktop-snapshot-push`` is accepted on this channel. The clone the
    dashboard trusts still comes from ``<state>/clone-inventory`` (the clone push), never from this snapshot's copy."""
    name = vs.ingest_bundle(raw, key, state_dir)
    return "ingested-snapshot " + name


def ingest_records(raw: bytes, key: bytes, state_dir: str | os.PathLike[str]) -> str:
    """The masked production create records from the PC, MERGED into the current snapshot (owner request 2026-10-27).

    Exactly one file (``production_create_records.json``), the same HMAC and size rules as every bundle, and every record is
    validated again by the desktop's own validator when the snapshot folder is written. Everything else in the current snapshot
    (queue, CO details, inputs, clone copy, state files) is carried over unchanged, with the current producer label, so the
    banner still says where the Salesforce data came from. No current snapshot means refusal; the same records twice are a
    no-op (no new snapshot)."""
    bundle = vs.verify_bundle(raw, key)
    if vs.PRODUCTION_RECORDS_FILE not in bundle.files or not set(bundle.files) <= {vs.PRODUCTION_RECORDS_FILE,
                                                                                    vs.PRODUCTION_APPROVALS_FILE}:
        raise PushRefused("records_bundle_has_other_files")
    if vs.PRODUCTION_APPROVALS_FILE in bundle.files:  # display copy of the create approvals: strictly the desktop's own format
        from integration.onboarding import production_views

        try:
            approvals = json.loads(bundle.files[vs.PRODUCTION_APPROVALS_FILE].decode("utf-8"))
            valid = isinstance(approvals, dict) and all(
                production_views._valid_approval(key_, record) for key_, record in approvals.items())  # noqa: SLF001
        except (ValueError, UnicodeDecodeError):
            valid = False
        if not valid:
            raise PushRefused("approvals_invalid")
    root = vs.snapshots_root(state_dir)
    manifest = vs.read_current_manifest(root)
    current = vs.current_dir(root)
    if manifest is None or current is None:
        raise PushRefused("no_current_snapshot")
    merged: dict[str, bytes] = {}
    for entry in manifest["files"]:
        name = entry["name"]
        if name in bundle.files:  # replaced by the pushed copy below
            continue
        data = (current / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise PushRefused("current_snapshot_damaged")
        merged[name] = data
    current_hashes = {entry["name"]: entry["sha256"] for entry in manifest["files"]}
    if all(current_hashes.get(name) == hashlib.sha256(data).hexdigest() for name, data in bundle.files.items()):
        return "unchanged-records"
    merged.update(bundle.files)
    created = max(datetime.now(timezone.utc).replace(microsecond=0), vs.parse_created_at(manifest["created_at"]))
    name = vs.ingest_bundle(vs.build_bundle(merged, key, str(manifest["producer"]), created), key, state_dir)
    return "ingested-records " + name


def _terminate(signum, frame):  # noqa: ARG001 - lets the ``finally`` cleanup run on SIGTERM / SSH hangup
    raise SystemExit(128 + signum)


def main(argv: list[str] | None = None, stdin: BinaryIO | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and ingest a clone-only bundle read from stdin.")
    parser.add_argument("--key-file", required=True, help="HMAC key file (root-owned, outside the repository).")
    parser.add_argument("--state-dir", default=os.environ.get("SURFACE_ONBOARDING_STATE_DIR", "") or DEFAULT_STATE_DIR)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.state_dir):
        print("refused:state_dir_not_absolute")
        return 64
    try:
        key = vs.load_key(args.key_file)
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), _terminate)
        raw = read_stdin_limited(stdin if stdin is not None else sys.stdin.buffer)
        producer = vs.verify_bundle(raw, key).manifest.get("producer")
        if producer == vs.SNAPSHOT_PUSH_PRODUCER:
            print(ingest_snapshot(raw, key, args.state_dir))
        elif producer == vs.RECORDS_PUSH_PRODUCER:
            print(ingest_records(raw, key, args.state_dir))
        else:
            print(ingest(raw, key, args.state_dir, min_rows=_min_rows_override()))
    except vs.SnapshotError as error:
        print("refused:" + error.code)
        return 2
    except PushRefused as error:
        print("refused:" + error.code)
        return 2
    except Exception:  # noqa: BLE001 - never echo a traceback, path or value
        print("refused:unexpected_error")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
