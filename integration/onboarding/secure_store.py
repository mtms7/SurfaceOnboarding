"""Encrypted local store for dashboard accounts and the activity audit log (owner request 2026-11-12, phase 1b).

Same code on the Windows desktop and the Ubuntu VM. Everything at rest is AES-256-GCM encrypted with a 32-byte key kept
in a file outside the repository (created on first use; never printed, logged or copied between hosts):

- ``accounts.enc``: one encrypted JSON document (atomic replace on every change).
- ``audit.log.enc``: append-only, one encrypted line per entry. Each entry carries the SHA-256 of the previous entry
  (``prev``), so a deleted, reordered or edited line breaks the chain and ``verify_audit`` reports where.

The associated data binds every blob to its purpose, so an accounts blob cannot be replayed as an audit line or back.
No password, hash, token or form content is ever written to the audit log; callers pass only short fields.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_BYTES = 32
NONCE_BYTES = 12
MAGIC = b"SOS1"
ACCOUNTS_FILE = "accounts.enc"
AUDIT_FILE = "audit.log.enc"
KEY_FILE = "store.key"
ACCOUNTS_AAD = b"surface-onboarding/accounts/v1"
AUDIT_AAD = b"surface-onboarding/audit/v1"
GENESIS = "0" * 64
MAX_ACCOUNTS_BYTES = 1024 * 1024
AUDIT_FIELD_LIMIT = 200


class StoreError(RuntimeError):
    """The store is unreadable, tampered with, or the key is wrong. The message is a short reason code."""


def _private_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically; on POSIX the file is created 0600 (owner only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_or_create_key(path: Path) -> bytes:
    """The store key; created (random, owner-only) when the file does not exist yet. Never returned to a caller's logs."""
    if path.exists():
        data = path.read_bytes()
        if len(data) != KEY_BYTES:
            raise StoreError("key_invalid")
        return data
    key = AESGCM.generate_key(bit_length=256)
    _private_write(path, key)
    return key


def encrypt(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) < len(MAGIC) + NONCE_BYTES + 16 or not blob.startswith(MAGIC):
        raise StoreError("blob_invalid")
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + NONCE_BYTES:], aad)
    except InvalidTag as error:
        raise StoreError("decrypt_failed") from error


def _entry_hash(entry: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _clip(value: object) -> str:
    text = "" if value is None else str(value)
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:AUDIT_FIELD_LIMIT]


@dataclass
class AuditCheck:
    ok: bool
    entries: int
    broken_at: int | None = None  # 1-based line number of the first bad line
    reason: str = ""


class SecureStore:
    """Accounts document plus audit log in one folder, both encrypted with the folder's key file."""

    def __init__(self, folder: str | Path, key_path: str | Path | None = None):
        self.folder = Path(folder)
        self.key_path = Path(key_path) if key_path else self.folder / KEY_FILE
        self._lock = Lock()
        self._key: bytes | None = None
        self._last_hash: str | None = None

    # -- key ------------------------------------------------------------------------------------------------------------
    def key(self) -> bytes:
        if self._key is None:
            self._key = load_or_create_key(self.key_path)
        return self._key

    def exists(self) -> bool:
        return (self.folder / ACCOUNTS_FILE).exists()

    # -- accounts -------------------------------------------------------------------------------------------------------
    def load_accounts(self) -> dict[str, Any]:
        """The accounts document (``{"accounts": {...}}``); an empty one when the store is new. StoreError if tampered."""
        path = self.folder / ACCOUNTS_FILE
        if not path.exists():
            return {"version": 1, "accounts": {}}
        try:
            blob = path.read_bytes()
        except OSError as error:
            raise StoreError("accounts_unreadable") from error
        if len(blob) > MAX_ACCOUNTS_BYTES:
            raise StoreError("accounts_too_large")
        try:
            document = json.loads(decrypt(self.key(), blob, ACCOUNTS_AAD).decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise StoreError("accounts_invalid") from error
        if not isinstance(document, dict) or not isinstance(document.get("accounts"), dict):
            raise StoreError("accounts_invalid")
        return document

    def save_accounts(self, document: dict[str, Any]) -> None:
        data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with self._lock:
            _private_write(self.folder / ACCOUNTS_FILE, encrypt(self.key(), data, ACCOUNTS_AAD))

    # -- audit ----------------------------------------------------------------------------------------------------------
    def _read_audit_lines(self) -> list[bytes]:
        path = self.folder / AUDIT_FILE
        if not path.exists():
            return []
        return [line for line in path.read_bytes().splitlines() if line.strip()]

    def _decode_line(self, line: bytes) -> dict[str, Any]:
        try:
            blob = base64.b64decode(line, validate=True)
        except ValueError as error:
            raise StoreError("audit_line_invalid") from error
        entry = json.loads(decrypt(self.key(), blob, AUDIT_AAD).decode("utf-8"))
        if not isinstance(entry, dict):
            raise StoreError("audit_line_invalid")
        return entry

    def append_audit(self, kind: str, user: str = "", role: str = "", action: str = "", target: str = "",
                     result: str = "", *, now: datetime | None = None) -> dict[str, Any]:
        """Append one chained, encrypted entry. Only short text fields; never secrets or form contents."""
        with self._lock:
            if self._last_hash is None:
                lines = self._read_audit_lines()
                self._last_hash = _entry_hash(self._decode_line(lines[-1])) if lines else GENESIS
            entry = {"at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": _clip(kind),
                     "user": _clip(user), "role": _clip(role), "action": _clip(action), "target": _clip(target),
                     "result": _clip(result), "prev": self._last_hash}
            line = base64.b64encode(encrypt(self.key(), json.dumps(entry, sort_keys=True).encode("utf-8"), AUDIT_AAD))
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self.folder / AUDIT_FILE
            new_file = not path.exists()
            with path.open("ab") as handle:
                handle.write(line + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            if new_file and os.name != "nt":
                os.chmod(path, 0o600)
            self._last_hash = _entry_hash(entry)
            return entry

    def read_audit(self, limit: int = 200, *, user: str = "", kind: str = "") -> list[dict[str, Any]]:
        """Newest first, at most ``limit`` entries, optionally filtered. Unreadable lines are skipped (see verify_audit)."""
        entries: list[dict[str, Any]] = []
        for line in reversed(self._read_audit_lines()):
            try:
                entry = self._decode_line(line)
            except (StoreError, ValueError, UnicodeError):
                continue
            if (user and entry.get("user") != user) or (kind and entry.get("kind") != kind):
                continue
            entries.append(entry)
            if len(entries) >= limit:
                break
        return entries

    def verify_audit(self) -> AuditCheck:
        """Walk the whole chain: every line must decrypt and point at the hash of the line before it."""
        previous = GENESIS
        lines = self._read_audit_lines()
        for number, line in enumerate(lines, start=1):
            try:
                entry = self._decode_line(line)
            except (StoreError, ValueError, UnicodeError):
                return AuditCheck(False, len(lines), number, "unreadable")
            if entry.get("prev") != previous:
                return AuditCheck(False, len(lines), number, "chain_broken")
            previous = _entry_hash(entry)
        return AuditCheck(True, len(lines))
