"""Desktop: build the clone-only signed bundle that the restricted push sends to the VM (docs/51, interim path).

Reads ONLY the local production-clone inventory pair the Redash collector wrote (no Salesforce, no queue, no state files),
re-verifies it (hash, name, environment, at most 6 h old, not from the future) and writes one zip signed with the existing
snapshot HMAC key. Nothing is sent here: ``tools/run_clone_push.ps1`` does the transfer.

    python tools/build_clone_push_bundle.py --key-file C:\\path\\outside\\repo\\snapshot.key --out C:\\path\\bundle.zip

Prints counts and the output path only. Exit 0 = written, 2 = refused (short code), 64 = usage.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding import leonardo_inventory as inventory  # noqa: E402
from integration.onboarding import redash_inventory as redash  # noqa: E402
from integration.onboarding import vm_snapshot as vs  # noqa: E402
from tools import publish_vm_snapshot as publisher  # noqa: E402

PRODUCER = vs.CLONE_PUSH_PRODUCER
MAX_AGE = timedelta(hours=6)  # the production gate's own limit


class BuildRefused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build(key_file: str, out: Path, root: Path | None = None, now: datetime | None = None) -> dict[str, int]:
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    root = root or inventory.clone_root()
    try:
        key = vs.load_key(key_file)
        files = publisher._inventory_pair(root, "prod-clone", "redash-prod-clone-",  # noqa: SLF001 - one allow-listed reader
                                          vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT)
        if set(files) != {vs.INVENTORY_CLONE_LATEST, vs.INVENTORY_CLONE_SNAPSHOT}:
            raise BuildRefused("local_clone_missing")
        try:
            loaded = inventory.load_latest(root, "prod-clone", max_age=MAX_AGE, now=now)
            redash.validate_row_schema(loaded)  # v1 (unmarked) or a well-formed v2; never sign a malformed one
        except inventory.InventoryError as error:
            raise BuildRefused("row_schema_invalid" if error.reason == "inventory_row_schema_invalid"
                               else error.reason) from None
        vs.write_bundle(out, files, key, PRODUCER, now)
    except vs.SnapshotError as error:
        raise BuildRefused(error.code) from None
    return {"rows": int(loaded["row_count"]), "data_age_minutes": int(loaded["age_seconds"]) // 60,
            "bytes": Path(out).stat().st_size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the clone-only signed bundle (local files only; nothing is sent).")
    parser.add_argument("--key-file", required=True, help="HMAC key file (outside the repository).")
    parser.add_argument("--out", required=True, help="Bundle path to write (zip).")
    args = parser.parse_args(argv)
    try:
        counts = build(args.key_file, Path(args.out))
    except BuildRefused as refusal:
        print("refused:" + refusal.code)
        return 2
    except Exception:  # noqa: BLE001
        print("refused:unexpected_error")
        return 2
    print(" ".join(f"{name}={value}" for name, value in sorted(counts.items())))
    print("written " + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
