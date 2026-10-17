"""File-backed ONE-TIME launch ticket for the production create runner (docs/53).

The dashboard mints one ticket after the owner's typed confirmation and every gate; the runner consumes it at start. The
runner never accepts a flag, argument or environment value as proof: only a valid, unexpired, unconsumed, never-replayed ticket
file bound to this CO, this Salesforce revision and this approval reference, minted for the operator who issued the approval and
signed (HMAC-SHA256 over every field) with a key that lives OUTSIDE the repository and outside any synced folder.

One shared helper, ``ticket_root()``, decides the folder for BOTH minting and consuming (per-user, ``%LOCALAPPDATA%\\SurfaceOnboarding``
on Windows); it never falls back to the repository root and refuses a repository, OneDrive or Documents location. The key is
exactly 32 bytes, created atomically (temp file then hard link, so a reader never sees a half-written key), restricted to the
current user on Windows with ``icacls``. Consuming is an atomic rename, and each nonce is recorded so a COPIED ticket cannot be
replayed inside its TTL. There is no key rotation: delete the key file to invalidate every outstanding ticket (a new key is made
at the next mint). Residual limit: a process of the same user can read the key and mint a ticket, exactly as it can edit the
approval file. Opens no connection.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SUBDIR = "production-launch-tickets"
KEY_NAME = ".ticket.key"
USED_NAME = ".used-nonces"
ENV_ROOT = "SURFACE_ONBOARDING_TICKET_ROOT"
TTL = timedelta(minutes=2)
KEY_BYTES = 32
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_CO = re.compile(r"CO-[0-9]{4,10}")
_NONCE = re.compile(r"[0-9a-f]{32}")
_REPO = Path(__file__).resolve().parents[2]


class TicketRootError(RuntimeError):
    """No safe per-user folder exists for tickets and their key (refuse, never fall back to the repository)."""


def _unsafe(path: Path) -> bool:
    try:
        resolved = Path(path).resolve()
    except OSError:
        return True
    if resolved == _REPO or _REPO in resolved.parents:
        return True
    return any(part.casefold() == "documents" or "onedrive" in part.casefold() for part in resolved.parts)


def _remote(path: Path) -> bool:
    """True for a UNC / shared / network-drive location (the key and tickets must stay on a LOCAL drive)."""
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    if os.name == "nt":
        try:
            import ctypes

            drive = os.path.splitdrive(text)[0]
            return not drive or ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # DRIVE_REMOTE
        except Exception:
            return True
    return False


def ticket_root(environ: Mapping[str, str] | None = None) -> Path:
    """The ONE folder both the dashboard (mint) and the runner (consume) use. Raises TicketRootError when none is safe."""
    environ = os.environ if environ is None else environ
    configured = str(environ.get(ENV_ROOT, "")).strip()
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            raise TicketRootError("launch_ticket_root_invalid")
    elif os.name == "nt":
        base = str(environ.get("LOCALAPPDATA", "")).strip()
        if not base or not Path(base).is_absolute():
            raise TicketRootError("launch_ticket_root_invalid")
        root = Path(base) / "SurfaceOnboarding"
    else:
        root = Path.home() / ".local" / "state" / "surface-onboarding"
    if _unsafe(root) or _remote(root):
        raise TicketRootError("launch_ticket_root_unsafe")
    return root


# Set by the dashboard (tools layer, which may start processes) to restrict the folder to the current user on Windows
# (``icacls /inheritance:r``). This module itself starts no process.
HARDEN: Callable[[Path], None] | None = None


def _restrict(path: Path) -> None:
    """Run the hardening hook; any failure raises (fail closed: no ticket is minted in a folder that could not be restricted)."""
    if HARDEN is not None:
        HARDEN(path)


def _path(parent: Path, co: str) -> Path:
    return Path(parent) / SUBDIR / (co + ".json")


def _aware(moment: Any) -> bool:
    return isinstance(moment, datetime) and moment.tzinfo is not None and moment.utcoffset() is not None


def _read_key(parent: Path) -> bytes | None:
    """The key only when the file holds EXACTLY 64 hex characters (32 bytes); anything else is "missing"."""
    try:
        text = (Path(parent) / SUBDIR / KEY_NAME).read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None
    if len(text) != KEY_BYTES * 2 or not re.fullmatch(r"[0-9a-f]+", text):
        return None
    return bytes.fromhex(text)


def _make_key(parent: Path) -> bytes | None:
    """Create the key atomically: write a temp file fully, then hard-link it into place (fails if a valid key appeared)."""
    folder = Path(parent) / SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    _restrict(folder)
    target = folder / KEY_NAME
    temp = folder / (".key." + secrets.token_hex(4) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(KEY_BYTES))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, target)
        except FileExistsError:
            if _read_key(parent) is None:  # an invalid (empty / short / odd) key file: replace it atomically
                os.replace(temp, target)
        except OSError:
            if not target.exists():
                os.replace(temp, target)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    return _read_key(parent)


def _key(parent: Path, create: bool) -> bytes | None:
    return _read_key(parent) or (_make_key(parent) if create else None)


def _mac(key: bytes, body: Mapping[str, Any]) -> str:
    canonical = json.dumps({k: v for k, v in body.items() if k != "mac"}, sort_keys=True, separators=(",", ":"))
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def discard(parent: Path, co: str) -> None:
    """Delete an unused ticket (the dashboard calls this when the launch failed)."""
    try:
        _path(parent, co).unlink()
    except OSError:
        pass


def mint(parent: Path, co: str, revision: str, approval_ref: str, operator: str, now: datetime) -> Path:
    """Write the signed ticket (replacing an unused older one for the same CO). Raises ValueError on any invalid input."""
    if (not isinstance(co, str) or not _CO.fullmatch(co) or not isinstance(revision, str) or not revision
            or not isinstance(approval_ref, str) or not approval_ref or not isinstance(operator, str) or not operator.strip()
            or not _aware(now) or _unsafe(Path(parent))):
        raise ValueError("launch_ticket_invalid")
    moment = now.astimezone(timezone.utc)
    body: dict[str, Any] = {"co_reference": co, "revision": revision, "approval_ref": approval_ref, "operator": operator.strip(),
                            "issued_at": moment.strftime(_STAMP), "expires_at": (moment + TTL).strftime(_STAMP),
                            "nonce": secrets.token_hex(16)}
    folder = Path(parent) / SUBDIR
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        _restrict(folder)  # checked on EVERY mint, not only when the key is first created
    except Exception:
        raise ValueError("launch_ticket_invalid") from None
    key = _key(parent, True)
    if key is None:
        raise ValueError("launch_ticket_invalid")
    body["mac"] = _mac(key, body)
    target = _path(parent, co)
    temp = folder / (co + "." + secrets.token_hex(4) + ".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(body, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    return target


def _nonce_used(parent: Path, nonce: str) -> bool | None:
    try:
        return nonce in (Path(parent) / SUBDIR / USED_NAME).read_text(encoding="ascii").split()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return None  # unreadable: fail closed


def _remember(parent: Path, nonce: str) -> bool:
    try:
        fd = os.open(Path(parent) / SUBDIR / USED_NAME, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="ascii") as handle:
            handle.write(nonce + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False


def consume(parent: Path, co: str, revision: str, approval_ref: str, now: datetime) -> tuple[str | None, str]:
    """(operator, "") for a valid ticket, which is consumed by an atomic rename and its nonce recorded; else (None, reason)."""
    bad = (None, "launch_ticket_invalid")
    if not _aware(now) or not isinstance(co, str) or not _CO.fullmatch(co) or _unsafe(Path(parent)):
        return bad
    source = _path(parent, co)
    used = source.with_name(co + "." + secrets.token_hex(4) + ".used")
    try:
        os.rename(source, used)  # atomic: only one process can win
    except OSError:
        return bad
    try:
        body = json.loads(used.read_text(encoding="utf-8"))
        if not isinstance(body, dict):
            return bad
        key = _key(parent, False)
        nonce = body.get("nonce")
        if (key is None or not isinstance(nonce, str) or not _NONCE.fullmatch(nonce) or not isinstance(body.get("mac"), str)
                or not hmac.compare_digest(body["mac"], _mac(key, body))):
            return bad  # forged, edited or unsigned: the operator field is only trusted through the MAC
        if _nonce_used(parent, nonce) is not False:
            return bad  # a copied ticket cannot be replayed inside its TTL
        expires = datetime.strptime(body["expires_at"], _STAMP).replace(tzinfo=timezone.utc)
        issued = datetime.strptime(body["issued_at"], _STAMP).replace(tzinfo=timezone.utc)
        ok = (body["co_reference"] == co and body["revision"] == revision and body["approval_ref"] == approval_ref
              and isinstance(body["operator"], str) and body["operator"].strip()
              and issued <= now.astimezone(timezone.utc) < expires)
        if not ok or not _remember(parent, nonce):
            return bad
        return body["operator"].strip(), ""
    except (OSError, ValueError, KeyError, TypeError):
        return bad
