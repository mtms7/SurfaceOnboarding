"""File-backed ONE-TIME launch ticket for the production create runner (docs/53).

The dashboard mints one ticket after the owner's typed confirmation and every gate; the runner consumes it at start. The
runner never accepts a flag, argument or environment value as proof: only a valid, unexpired, unconsumed ticket file bound to
this CO, this Salesforce revision and this approval reference, minted for the operator who issued the approval. Consuming is an
atomic rename, so two runners cannot both use one ticket. Opens no connection; touches only its own folder.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SUBDIR = "production-launch-tickets"
TTL = timedelta(minutes=2)
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_CO = re.compile(r"CO-[0-9]{4,10}")


def _path(parent: Path, co: str) -> Path:
    return Path(parent) / SUBDIR / (co + ".json")


def _aware(moment: Any) -> bool:
    return isinstance(moment, datetime) and moment.tzinfo is not None and moment.utcoffset() is not None


def mint(parent: Path, co: str, revision: str, approval_ref: str, operator: str, now: datetime) -> Path:
    """Write the ticket (replacing an unused older one for the same CO). Raises ValueError on any invalid input."""
    if (not isinstance(co, str) or not _CO.fullmatch(co) or not isinstance(revision, str) or not revision
            or not isinstance(approval_ref, str) or not approval_ref or not isinstance(operator, str) or not operator.strip()
            or not _aware(now)):
        raise ValueError("launch_ticket_invalid")
    moment = now.astimezone(timezone.utc)
    body = {"co_reference": co, "revision": revision, "approval_ref": approval_ref, "operator": operator.strip(),
            "issued_at": moment.strftime(_STAMP), "expires_at": (moment + TTL).strftime(_STAMP),
            "nonce": secrets.token_hex(16)}
    folder = Path(parent) / SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = _path(parent, co)
    temp = folder / (co + "." + secrets.token_hex(4) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(body, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return target


def consume(parent: Path, co: str, revision: str, approval_ref: str, now: datetime) -> tuple[str | None, str]:
    """(operator, "") for a valid ticket, which is consumed by an atomic rename; else (None, reason code)."""
    if not _aware(now) or not isinstance(co, str) or not _CO.fullmatch(co):
        return None, "launch_ticket_invalid"
    source = _path(parent, co)
    used = source.with_name(co + "." + secrets.token_hex(4) + ".used")
    try:
        os.rename(source, used)  # atomic: only one process can win
    except OSError:
        return None, "launch_ticket_invalid"
    try:
        body = json.loads(used.read_text(encoding="utf-8"))
        expires = datetime.strptime(body["expires_at"], _STAMP).replace(tzinfo=timezone.utc)
        issued = datetime.strptime(body["issued_at"], _STAMP).replace(tzinfo=timezone.utc)
        ok = (body["co_reference"] == co and body["revision"] == revision and body["approval_ref"] == approval_ref
              and isinstance(body["operator"], str) and body["operator"].strip()
              and issued <= now.astimezone(timezone.utc) < expires)
        return (body["operator"].strip(), "") if ok else (None, "launch_ticket_invalid")
    except (OSError, ValueError, KeyError, TypeError):
        return None, "launch_ticket_invalid"
