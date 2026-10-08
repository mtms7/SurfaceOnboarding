"""Signed, allow-listed state snapshot for the read-only VM dashboard (docs/41, owner decisions 2026-10-08).

The desktop is the only writer of onboarding state. It publishes a masked snapshot as one zip; the VM verifies it,
unpacks it and switches a ``current`` pointer. The VM dashboard only reads ``current``.

Bundle layout (all members are flat file names, no folders):

* ``manifest.json``: ``schema_version``, ``created_at`` (UTC), ``producer`` label, ``files`` (name, size, sha256).
* ``manifest.sig``: hex HMAC-SHA256 of the canonical manifest bytes (sorted keys, no spaces, ASCII).
* the allow-listed data files named in the manifest, and nothing else.

The HMAC key lives in a file outside the repository (desktop: DPAPI-protected copy; VM: root-owned ``0640``). This
module never prints, logs or returns key material, and its errors carry a short code only, never file content.

Store layout on the VM (``<root>`` = ``<STATE_DIR>/snapshots``)::

    <root>/incoming/                 staging area (emptied on every ingest)
    <root>/snapshot-<UTC stamp>[-n]/ one verified snapshot per directory (the newest KEEP_SNAPSHOTS are kept)
    <root>/current                   symlink to one snapshot directory (POSIX)
    <root>/CURRENT                   fallback marker file holding the directory name (where symlinks are not used)

Pure standard library: no network, no process execution (tools/check_integration_offline_boundary.py).
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
SIGNATURE_NAME = "manifest.sig"
MAX_FILE_BYTES = 32 * 1024 * 1024  # owner decision 2026-10-08: the production clone snapshot is about 17 MB
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_MEMBERS = 64
MIN_KEY_BYTES = 32
KEEP_SNAPSHOTS = 5
SNAPSHOTS_SUBDIR = "snapshots"
INCOMING_DIR = "incoming"
CURRENT_LINK = "current"
CURRENT_MARKER = "CURRENT"
INVENTORY_SUBDIR = "leonardo-inventory"

QUEUE_FILE = "queue.json"
CO_DETAILS_FILE = "co_details.json"
INVENTORY_DEV_LATEST = "inventory_dev_latest.json"
INVENTORY_DEV_SNAPSHOT = "inventory_dev_snapshot.json"

# docs/40 section 5 state files (a file that does not exist on the desktop is simply not published), the run
# progress file named in docs/41 section 2, and the fixed-field queue / CO-detail exports. The production clone
# (Redash, ~17 MB) exceeds the per-file cap and is deliberately NOT on the list.
STATE_FILE_NAMES = frozenset({
    "attended_leonardo_readbacks.json", "attended_ce_only_runner_state.json", "attended_ce_only_diagnostics.json",
    "attended_ce_only_table_diagnostics.json", "attended_ce_only_run_log.json", "attended_ce_only_check_state.json",
    "attended_ce_only_progress.json", "attended_scan_reminders.json", "attended_scan_status.json",
    "attended_user_created_confirmations.json", "attended_spycloud.json", "attended_salesforce_id_writebacks.json",
    "attended_surface_validation.json", "attended_renewal_mirrors.json", "attended_renewal_outcomes.json",
})
EXPORT_FILE_NAMES = frozenset({QUEUE_FILE, CO_DETAILS_FILE, INVENTORY_DEV_LATEST, INVENTORY_DEV_SNAPSHOT})
ALLOWED_NAMES = STATE_FILE_NAMES | EXPORT_FILE_NAMES

_SNAPSHOT_DIR = re.compile(r"snapshot-[0-9]{8}T[0-9]{6}Z(?:-[0-9]{1,3})?")
_CREATED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_PRODUCER = re.compile(r"[A-Za-z0-9._ -]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# Same shape as integration.onboarding.leonardo_inventory.SNAPSHOT_FILE (kept local: this module is stand-alone).
_INVENTORY_FILE = re.compile(r"(?:(?:leonardo-dev|backoffice-prod|redash-prod-clone)-)?inventory-\d{8}T\d{6}Z\.json")
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class SnapshotError(Exception):
    """A refusal. ``code`` is a short stable identifier; the message never contains file content or key material."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def parse_created_at(value: Any) -> datetime:
    if type(value) is not str or not _CREATED_AT.fullmatch(value):
        raise SnapshotError("bad_created_at")
    return datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=timezone.utc)


# ----- key and signature ---------------------------------------------------------------------------------------

def load_key(path: str | os.PathLike[str]) -> bytes:
    """The HMAC key from a file (surrounding whitespace removed). Refuses a missing, unreadable or short key."""
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise SnapshotError("key_unreadable") from error
    key = raw.strip()
    if len(key) < MIN_KEY_BYTES or len(key) > 4096:
        raise SnapshotError("key_invalid")
    return key


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sign_manifest(manifest: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, canonical_manifest_bytes(manifest), hashlib.sha256).hexdigest()


# ----- names ---------------------------------------------------------------------------------------------------

def _plain_name(name: Any) -> bool:
    """A flat, relative, printable ASCII file name: no separators, drive letters, traversal or control characters."""
    return (type(name) is str and 0 < len(name) <= 96 and name not in (".", "..")
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is not None and ".." not in name)


def _check_member_name(name: Any) -> None:
    if not _plain_name(name):
        raise SnapshotError("bad_member_name")


# ----- building (desktop) --------------------------------------------------------------------------------------

def build_manifest(files: Mapping[str, bytes], producer: str, created_at: datetime | None = None) -> dict[str, Any]:
    if not _PRODUCER.fullmatch(producer or ""):
        raise SnapshotError("bad_producer")
    entries = []
    for name in sorted(files):
        if name not in ALLOWED_NAMES:
            raise SnapshotError("name_not_allowed")
        data = files[name]
        if type(data) is not bytes:
            raise SnapshotError("bad_file_content")
        if len(data) > MAX_FILE_BYTES:
            raise SnapshotError("file_too_large")
        entries.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    if not entries:
        raise SnapshotError("empty_bundle")
    if sum(entry["size"] for entry in entries) > MAX_BUNDLE_BYTES:
        raise SnapshotError("bundle_too_large")
    return {"schema_version": SCHEMA_VERSION, "created_at": _stamp(created_at or _utcnow()), "producer": producer,
            "files": entries}


def build_bundle(files: Mapping[str, bytes], key: bytes, producer: str, created_at: datetime | None = None) -> bytes:
    """The signed zip as bytes. Refuses any name that is not on the allow-list (nothing is skipped silently)."""
    manifest = build_manifest(files, producer, created_at)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in (MANIFEST_NAME, SIGNATURE_NAME, *[entry["name"] for entry in manifest["files"]]):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o640) << 16
            if name == MANIFEST_NAME:
                data = canonical_manifest_bytes(manifest)
            elif name == SIGNATURE_NAME:
                data = sign_manifest(manifest, key).encode("ascii")
            else:
                data = files[name]
            archive.writestr(info, data)
    return buffer.getvalue()


def write_bundle(path: str | os.PathLike[str], files: Mapping[str, bytes], key: bytes, producer: str,
                 created_at: datetime | None = None) -> Path:
    """Write the bundle atomically (temporary file, then replace). Returns the final path."""
    data = build_bundle(files, key, producer, created_at)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


# ----- verifying (VM) ------------------------------------------------------------------------------------------

class VerifiedBundle:
    """The result of a successful verification: the manifest and the verified file contents."""

    def __init__(self, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        self.manifest = manifest
        self.files = files

    @property
    def created_at(self) -> datetime:
        return parse_created_at(self.manifest["created_at"])


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    limit = info.file_size
    try:
        with archive.open(info) as handle:
            data = handle.read(limit + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, ValueError, EOFError) as error:
        raise SnapshotError("bad_zip") from error
    if len(data) != limit:
        raise SnapshotError("size_mismatch")
    return data


def verify_bundle(source: str | os.PathLike[str] | bytes, key: bytes) -> VerifiedBundle:
    """Verify a bundle completely before anything is written anywhere. Raises SnapshotError on any problem."""
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        if len(raw) > MAX_BUNDLE_BYTES + 1024 * 1024:
            raise SnapshotError("bundle_too_large")
        stream: Any = io.BytesIO(raw)
    else:
        try:
            if Path(source).stat().st_size > MAX_BUNDLE_BYTES + 1024 * 1024:
                raise SnapshotError("bundle_too_large")
        except OSError as error:
            raise SnapshotError("bundle_unreadable") from error
        stream = str(source)
    try:
        archive = zipfile.ZipFile(stream)
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise SnapshotError("bad_zip") from error
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise SnapshotError("bad_member_count")
        seen: set[str] = set()
        total = 0
        for info in infos:
            name = info.filename
            if name in seen:
                raise SnapshotError("duplicate_member")
            seen.add(name)
            if info.is_dir() or "/" in name or "\\" in name or name.startswith(("/", "~")) or ".." in name or ":" in name:
                raise SnapshotError("bad_member_name")
            _check_member_name(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (stat.S_ISLNK(mode) or stat.S_ISDIR(mode) or (mode & 0o170000 and not stat.S_ISREG(mode))):
                raise SnapshotError("link_member")
            if info.flag_bits & 0x1:
                raise SnapshotError("encrypted_member")
            if info.file_size > MAX_FILE_BYTES:
                raise SnapshotError("file_too_large")
            total += info.file_size
            if total > MAX_BUNDLE_BYTES:
                raise SnapshotError("bundle_too_large")
        by_name = {info.filename: info for info in infos}
        if MANIFEST_NAME not in by_name or SIGNATURE_NAME not in by_name:
            raise SnapshotError("manifest_missing")
        manifest_bytes = _read_member(archive, by_name[MANIFEST_NAME])
        signature = _read_member(archive, by_name[SIGNATURE_NAME]).strip()
        try:
            manifest = json.loads(manifest_bytes.decode("ascii"))
        except (UnicodeError, ValueError) as error:
            raise SnapshotError("manifest_invalid") from error
        if not isinstance(manifest, dict):
            raise SnapshotError("manifest_invalid")
        try:
            expected = sign_manifest(manifest, key)
        except (TypeError, ValueError) as error:
            raise SnapshotError("manifest_invalid") from error
        if not hmac.compare_digest(signature, expected.encode("ascii")):
            raise SnapshotError("bad_signature")
        # The signature holds: the manifest is the desktop's. Now check it against the schema and the archive.
        if manifest.get("schema_version") != SCHEMA_VERSION or type(manifest.get("schema_version")) is not int:
            raise SnapshotError("schema_version")
        if set(manifest) != {"schema_version", "created_at", "producer", "files"}:
            raise SnapshotError("manifest_invalid")
        parse_created_at(manifest["created_at"])
        if type(manifest["producer"]) is not str or not _PRODUCER.fullmatch(manifest["producer"]):
            raise SnapshotError("manifest_invalid")
        entries = manifest["files"]
        if type(entries) is not list or not entries:
            raise SnapshotError("manifest_invalid")
        listed: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != {"name", "size", "sha256"}
                    or type(entry["size"]) is not int or type(entry["sha256"]) is not str
                    or not _SHA256.fullmatch(entry["sha256"])):
                raise SnapshotError("manifest_invalid")
            name = entry["name"]
            _check_member_name(name)
            if name in listed:
                raise SnapshotError("duplicate_member")
            if name not in ALLOWED_NAMES:
                raise SnapshotError("name_not_allowed")
            if entry["size"] < 0 or entry["size"] > MAX_FILE_BYTES:
                raise SnapshotError("file_too_large")
            listed[name] = entry
        archive_names = set(by_name) - {MANIFEST_NAME, SIGNATURE_NAME}
        if archive_names - set(listed):
            raise SnapshotError("extra_file")
        if set(listed) - archive_names:
            raise SnapshotError("missing_file")
        files: dict[str, bytes] = {}
        for name, entry in listed.items():
            data = _read_member(archive, by_name[name])
            if len(data) != entry["size"]:
                raise SnapshotError("size_mismatch")
            if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), entry["sha256"]):
                raise SnapshotError("hash_mismatch")
            files[name] = data
    _check_inventory_pair(files)
    return VerifiedBundle(manifest, files)


def _check_inventory_pair(files: Mapping[str, bytes]) -> None:
    """The Dev inventory is published as a pair; ``latest`` must name a well-formed snapshot file."""
    has_latest, has_snapshot = INVENTORY_DEV_LATEST in files, INVENTORY_DEV_SNAPSHOT in files
    if has_latest != has_snapshot:
        raise SnapshotError("inventory_incomplete")
    if has_latest:
        _inventory_file_name(files[INVENTORY_DEV_LATEST])


def _inventory_file_name(latest_bytes: bytes) -> str:
    try:
        latest = json.loads(latest_bytes.decode("utf-8"))
        name = latest["file"]
    except (UnicodeError, ValueError, KeyError, TypeError) as error:
        raise SnapshotError("inventory_invalid") from error
    if type(name) is not str or not _INVENTORY_FILE.fullmatch(name):
        raise SnapshotError("inventory_invalid")
    return name


# ----- the store (VM) ------------------------------------------------------------------------------------------

def _use_symlink() -> bool:
    return os.name != "nt"


def snapshots_root(state_dir: str | os.PathLike[str]) -> Path:
    return Path(state_dir) / SNAPSHOTS_SUBDIR


def current_name(root: Path) -> str | None:
    """The directory name the pointer designates, or None. The single place that reads the pointer."""
    root = Path(root)
    link = root / CURRENT_LINK
    name: str | None = None
    try:
        if link.is_symlink():
            name = os.path.basename(os.readlink(link))
        elif (root / CURRENT_MARKER).is_file():
            name = (root / CURRENT_MARKER).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if name is None or not _SNAPSHOT_DIR.fullmatch(name) or not (root / name).is_dir() or (root / name).is_symlink():
        return None
    return name


def current_dir(root: Path) -> Path | None:
    """The directory of the current snapshot, or None when none has been ingested."""
    name = current_name(Path(root))
    return None if name is None else Path(root) / name


def read_current_manifest(root: Path) -> dict[str, Any] | None:
    """The current snapshot's manifest (already verified at ingest), or None."""
    directory = current_dir(root)
    if directory is None:
        return None
    try:
        manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        parse_created_at(manifest["created_at"])
    except (OSError, ValueError, KeyError, TypeError, SnapshotError):
        return None
    return manifest if isinstance(manifest, dict) else None


def load_current_json(root: Path, name: str) -> Any:
    """One allow-listed JSON file of the current snapshot. Raises SnapshotError when absent or unreadable."""
    if name not in ALLOWED_NAMES:
        raise SnapshotError("name_not_allowed")
    directory = current_dir(root)
    if directory is None:
        raise SnapshotError("no_snapshot")
    try:
        return json.loads((directory / name).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SnapshotError("file_unavailable") from error


def list_snapshots(root: Path) -> list[str]:
    """Snapshot directory names, oldest first."""
    try:
        names = [entry.name for entry in Path(root).iterdir()
                 if _SNAPSHOT_DIR.fullmatch(entry.name) and entry.is_dir() and not entry.is_symlink()]
    except OSError:
        return []
    return sorted(names, key=lambda item: (item[:25], len(item), item))


def _switch_pointer(root: Path, name: str) -> None:
    if _use_symlink():
        temporary = root / f"{CURRENT_LINK}.tmp-{os.getpid()}"
        temporary.unlink(missing_ok=True)
        os.symlink(name, temporary)
        try:
            os.replace(temporary, root / CURRENT_LINK)
        finally:
            if temporary.is_symlink():
                temporary.unlink(missing_ok=True)
    else:
        temporary = root / f"{CURRENT_MARKER}.tmp-{os.getpid()}"
        try:
            temporary.write_text(name, encoding="ascii")
            os.replace(temporary, root / CURRENT_MARKER)
        finally:
            temporary.unlink(missing_ok=True)


def _prune(root: Path, keep: int, protect: str) -> list[str]:
    removed = []
    names = list_snapshots(root)
    for name in names[:-keep] if len(names) > keep else []:
        if name != protect:
            shutil.rmtree(root / name, ignore_errors=True)
            removed.append(name)
    return removed


def _write_snapshot_dir(stage: Path, bundle: VerifiedBundle) -> None:
    stage.mkdir(parents=True)
    for name, data in bundle.files.items():
        (stage / name).write_bytes(data)
    # The dashboard's inventory reader expects <root>/<env>/latest.json plus the named snapshot file.
    if INVENTORY_DEV_LATEST in bundle.files:
        file_name = _inventory_file_name(bundle.files[INVENTORY_DEV_LATEST])
        inventory = stage / INVENTORY_SUBDIR / "dev"
        inventory.mkdir(parents=True)
        (inventory / "latest.json").write_bytes(bundle.files[INVENTORY_DEV_LATEST])
        (inventory / file_name).write_bytes(bundle.files[INVENTORY_DEV_SNAPSHOT])
    (stage / MANIFEST_NAME).write_bytes(canonical_manifest_bytes(bundle.manifest))


def ingest_bundle(source: str | os.PathLike[str] | bytes, key: bytes, state_dir: str | os.PathLike[str],
                  keep: int = KEEP_SNAPSHOTS) -> str:
    """Verify, unpack to ``incoming/``, atomically switch ``current``, prune. Returns the new snapshot's directory name.

    The pointer moves only after everything succeeded; any failure leaves the previous snapshot current and intact.
    A bundle older than the current snapshot is refused (no rollback by replay).
    """
    if type(keep) is not int or keep < 1:
        raise SnapshotError("bad_keep")
    bundle = verify_bundle(source, key)
    root = snapshots_root(state_dir)
    incoming = root / INCOMING_DIR
    try:
        root.mkdir(parents=True, exist_ok=True)
        previous = read_current_manifest(root)
        if previous is not None and bundle.created_at < parse_created_at(previous["created_at"]):
            raise SnapshotError("older_than_current")
        shutil.rmtree(incoming, ignore_errors=True)
        incoming.mkdir(parents=True)
        base = "snapshot-" + bundle.created_at.strftime("%Y%m%dT%H%M%SZ")
        name, counter = base, 1
        while (root / name).exists():
            counter += 1
            name = f"{base}-{counter}"
        stage = incoming / name
        _write_snapshot_dir(stage, bundle)
        os.replace(stage, root / name)
        try:
            _switch_pointer(root, name)
        except OSError:
            shutil.rmtree(root / name, ignore_errors=True)
            raise
    except SnapshotError:
        shutil.rmtree(incoming, ignore_errors=True)
        raise
    except OSError as error:
        shutil.rmtree(incoming, ignore_errors=True)
        raise SnapshotError("store_unavailable") from error
    shutil.rmtree(incoming, ignore_errors=True)
    _prune(root, keep, protect=name)
    return name
