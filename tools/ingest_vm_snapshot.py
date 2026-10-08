"""VM: verify a published snapshot zip, unpack it under <STATE_DIR>/snapshots, switch ``current`` (docs/41).

    python3 tools/ingest_vm_snapshot.py --key-file /etc/surface-onboarding/snapshot.key --zip /path/snapshot.zip

STATE_DIR is SURFACE_ONBOARDING_STATE_DIR (default /var/lib/surface-onboarding). Stdlib only. Prints the new snapshot
directory name on success and a short refusal code on failure; never any content. Any failure leaves the previous
snapshot current and intact. Exit 0 = ingested, 2 = refused or failed, 64 = usage.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import vm_snapshot  # noqa: E402

DEFAULT_STATE_DIR = "/var/lib/surface-onboarding"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and ingest a signed VM snapshot zip.")
    parser.add_argument("--key-file", required=True, help="HMAC key file (root-owned, outside the repository).")
    parser.add_argument("--zip", required=True, dest="bundle", help="The snapshot zip copied from the desktop.")
    parser.add_argument("--state-dir", default=os.environ.get("SURFACE_ONBOARDING_STATE_DIR", "") or DEFAULT_STATE_DIR)
    args = parser.parse_args(argv)
    if not os.path.isabs(args.state_dir):
        print("refused:state_dir_not_absolute")
        return 64
    try:
        key = vm_snapshot.load_key(args.key_file)
        name = vm_snapshot.ingest_bundle(args.bundle, key, args.state_dir)
    except vm_snapshot.SnapshotError as error:
        print("refused:" + error.code)
        return 2
    except Exception:  # noqa: BLE001 - never print content or a traceback that could echo a path or value
        print("refused:unexpected_error")
        return 2
    print("ingested " + name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
