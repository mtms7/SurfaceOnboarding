"""Clear ONE CO's local dashboard records so a rehearsed onboarding can be run again (local files only).

Use after the rehearsal tenant was DELETED in Leonardo Development (a rename is not enough: the live
duplicate check also matches the primary and alternate domains). It never contacts Leonardo, Salesforce
or Redash. Default is a dry run that lists what would be removed; ``--confirm-tenant-deleted`` applies it,
after copying every touched file to ``integration/attended_reset_backups/<timestamp>/``.

    python tools/reset_demo_co.py --co CO-0765
    python tools/reset_demo_co.py --co CO-0765 --confirm-tenant-deleted
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding.state_paths import state_file  # noqa: E402

REFERENCE = re.compile(r"CO-[0-9]{4,6}")
# Per-CO dictionaries (key = CO number). The run log is a list of runs and is left as history.
STATE_FILES = (
    "attended_ce_only_runner_state.json",
    "attended_leonardo_readbacks.json",
    "attended_ce_only_check_state.json",
    "attended_scan_status.json",
    "attended_spycloud.json",
    "attended_surface_validation.json",
    "attended_renewal_mirrors.json",
    "attended_renewal_outcomes.json",
)
BACKUP_ROOT = ROOT / "integration" / "attended_reset_backups"


def _load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def plan(reference: str) -> list[tuple[Path, dict]]:
    """Files that hold an entry for ``reference`` (nothing is changed)."""
    found = []
    for name in STATE_FILES:
        path = state_file(ROOT / "integration" / name)
        data = _load(path)
        if data is not None and reference in data:
            found.append((path, data))
    return found


def apply(reference: str, found: list[tuple[Path, dict]]) -> Path:
    backup = BACKUP_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=False)
    for path, data in found:
        shutil.copy2(path, backup / path.name)
        remaining = {key: value for key, value in data.items() if key != reference}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(remaining, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--co", required=True, help="CO number, for example CO-0765")
    parser.add_argument("--confirm-tenant-deleted", action="store_true",
                        help="I deleted this CO's Leonardo Development tenant; remove its local records.")
    args = parser.parse_args(argv)
    if not REFERENCE.fullmatch(args.co):
        parser.error("--co must look like CO-0765")
    found = plan(args.co)
    names = [path.name for path, _ in found]
    if not args.confirm_tenant_deleted:
        print(json.dumps({"result": "reset_dry_run", "co": args.co, "would_clear": names, "changed": False}))
        return 0
    if not found:
        print(json.dumps({"result": "reset_nothing_to_clear", "co": args.co, "changed": False}))
        return 0
    backup = apply(args.co, found)
    print(json.dumps({"result": "reset_done", "co": args.co, "cleared": names,
                      "backup": str(backup.relative_to(ROOT)), "changed": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
