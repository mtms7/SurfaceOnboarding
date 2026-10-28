"""VM: refresh the queue and the CO details from Salesforce (read-only) into a new signed snapshot (docs/43 part A).

Run by a systemd timer as the dashboard's service user with the reader's credential group. It reuses the dashboard's two
fixed reads (``queue_source_rows`` and ``detail_row``: same SOQL, same fields) through the read-only REST client in
``tools/vm_salesforce_reader.py``, keeps every other file of the current snapshot (run history and Dev
inventory published from the desktop), signs the result and ingests it locally.

    # pilot (owner decision 2026-10-14): an operator's own SSO session, kept only in memory (tmpfs), never on disk
    python3 tools/vm_refresh_snapshot.py --transport cli --session-dir /run/surface-onboarding-sf \\
        --key-file /etc/surface-onboarding/snapshot.key

    # later: a read-only service identity (OAuth JWT bearer), no human session
    python3 tools/vm_refresh_snapshot.py --transport rest --config /etc/surface-onboarding/sf-reader.json \\
        --key-file /etc/surface-onboarding/snapshot.key

    python3 tools/vm_refresh_snapshot.py --dry-run     # checks configuration and key files; no network, no write

Fail closed: any problem leaves the previous snapshot current and intact (the banner shows its age). A CO whose detail
read fails keeps its previous detail. No Salesforce write exists in this path. Output is counts and short reason codes only.
Every run records ``refresh-status.json`` in the state directory (time, result code, transport; no values) so the dashboard
can say "Salesforce session needed" instead of silently showing old data.
Exit 0 = refreshed (or dry run), 2 = refused, 3 = Salesforce read unavailable or session needed, 64 = usage, 75 = another
refresh is running.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from integration.onboarding import vm_snapshot  # noqa: E402
from tools import vm_salesforce_reader as reader  # noqa: E402

DEFAULT_STATE_DIR = "/var/lib/surface-onboarding"
LOCK_NAME = "vm-refresh.lock"
STATUS_NAME = "refresh-status.json"
SESSION_DIR_ENV = "SURFACE_SF_SESSION_DIR"
# The CLI session must live in memory: only these tmpfs roots are accepted (never the state dir or a disk path).
MEMORY_ROOTS = ("/run/", "/dev/shm/")
SESSION_ENV = {"SF_USE_GENERIC_UNIX_KEYCHAIN": "true", "SF_DISABLE_TELEMETRY": "true", "SF_AUTOUPDATE_DISABLE": "true",
               "SFDX_AUTOUPDATE_DISABLE": "true", "SF_SKIP_NEW_VERSION_CHECK": "true", "SF_DISABLE_LOG_FILE": "true"}
CARRY_EXCLUDED = frozenset({vm_snapshot.QUEUE_FILE, vm_snapshot.CO_DETAILS_FILE, vm_snapshot.PRODUCTION_INPUTS_FILE})


class RefreshRefused(Exception):
    def __init__(self, code: str, exit_code: int = 2) -> None:
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def session_dir_problem(path: str) -> str | None:
    """Reason code when ``path`` is not an acceptable in-memory CLI session folder, else None.

    It must be under a tmpfs root (``/run/`` or ``/dev/shm/``), a real directory (not a link), private to the running
    user, and it must already hold a session (an auth file for a signed-in user): otherwise the operator has not signed in.
    """
    if not isinstance(path, str) or not os.path.isabs(path):
        return "session_dir_not_absolute"
    real = os.path.realpath(path)
    if not real.startswith(MEMORY_ROOTS) or os.path.realpath(path + "/") != real or os.path.islink(path):
        return "session_dir_not_in_memory"
    try:
        info = os.stat(real)
    except OSError:
        return "session_needed"
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
        return "session_dir_unsafe"
    # A signed-in CLI keeps one auth file per user (``~/.sfdx/<username>.json``); folders the CLI creates on any run (``.sf``,
    # ``.cache``) are not a session.
    auth = os.path.join(real, ".sfdx")
    try:
        if not any(name.endswith(".json") and "@" in name for name in os.listdir(auth)):
            return "session_needed"
    except OSError:
        return "session_needed"
    return None


def write_status(state_dir: str, result: str, transport: str, now: datetime | None = None) -> None:
    """Record the outcome of this run (no values) for the dashboard banner; best effort, never raises."""
    try:
        stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        target = Path(state_dir) / STATUS_NAME
        handle, temp_name = tempfile.mkstemp(prefix=STATUS_NAME + ".", suffix=".tmp", dir=state_dir)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": stamp, "result": result, "transport": transport}, sort_keys=True) + "\n")
        os.replace(temp_name, target)
    except OSError:
        pass


def status_result(code: str | None, transport: str) -> str:
    """The value-free result stored for the banner."""
    if code is None:
        return "ok"
    if code == "session_needed" or (transport == "cli" and code == "queue_unavailable"):
        return "session_needed"
    return "error"


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


def previous_production_inputs(state_dir: str) -> dict[str, Any]:
    """The production-check inputs of the current snapshot (empty when there is none or it is unreadable)."""
    try:
        data = vm_snapshot.load_current_json(vm_snapshot.snapshots_root(state_dir), vm_snapshot.PRODUCTION_INPUTS_FILE)
        inputs = data.get("inputs") if isinstance(data, dict) else None
    except vm_snapshot.SnapshotError:
        return {}
    return inputs if isinstance(inputs, dict) else {}


def refresh(config_path: str, key_file: str, state_dir: str, *, opener: Any | None = None,
            sign: Callable[[str, bytes], bytes] = reader.openssl_sign,
            now: datetime | None = None, dashboard: Any | None = None, transport: str = "rest",
            session_dir: str = "") -> dict[str, int]:
    if transport not in ("rest", "cli"):
        raise RefreshRefused("unknown_transport")
    config = None
    try:
        key = vm_snapshot.load_key(key_file)
        if transport == "rest":
            config = reader.load_config(config_path)
            reader._private_file(Path(config.private_key_file), secret=True, reason="key_file_unsafe")
    except vm_snapshot.SnapshotError as error:
        raise RefreshRefused(error.code) from None
    except reader.ReaderError as error:
        raise RefreshRefused(error.reason) from None
    if transport == "cli":
        problem = session_dir_problem(session_dir)
        if problem is not None:
            raise RefreshRefused(problem, 3 if problem == "session_needed" else 2)
    if dashboard is None:
        import tools.serve_attended_open_onboardings_dashboard as dashboard  # noqa: PLW0127
    if dashboard.state_paths.vm_mode():
        raise RefreshRefused("runtime_must_not_be_vm")  # VM mode serves snapshots; this process must read Salesforce live
    carried, previous_details = carried_files(state_dir)
    original = dashboard.sf_json
    saved_env = {name: os.environ.get(name) for name in (*SESSION_ENV, "HOME")}
    import tools.attended_ce_only_playwright as runner  # the production-check inputs are read through its own query helper

    original_records = runner._sf_records
    if transport == "rest":
        client = reader.RestQueryClient(config, opener, sign, clock=lambda: now or datetime.now(timezone.utc))
        dashboard.sf_json = reader.make_sf_json(client, dashboard.ReadUnavailable)

        def rest_records(query: str) -> list[Any]:
            try:
                return client.query(query)
            except reader.ReaderError as error:
                raise ValueError("salesforce_rest_unavailable") from error

        runner._sf_records = rest_records
    else:  # the dashboard's own CLI reads, with the CLI's whole home folder in memory
        os.environ.update(SESSION_ENV)
        os.environ["HOME"] = os.path.realpath(session_dir)
    try:
        import tools.publish_vm_snapshot as publisher

        try:
            failed_countries: set[str] = set()
            reads, counts = publisher.collect_dashboard_reads(sorted(previous_details), failed_countries)
        except publisher.PublishRefused as refusal:
            raise RefreshRefused(refusal.code, refusal.exit_code) from None
    finally:
        dashboard.sf_json = original
        runner._sf_records = original_records
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    details = json.loads(reads[vm_snapshot.CO_DETAILS_FILE].decode("utf-8"))["details"]
    kept = 0
    for reference, detail in previous_details.items():  # a transient failure must not drop a CO's page
        if reference not in details:
            details[reference] = detail
            kept += 1
    reads[vm_snapshot.CO_DETAILS_FILE] = json.dumps({"details": details}, sort_keys=True).encode("utf-8")
    fresh_inputs = json.loads(reads[vm_snapshot.PRODUCTION_INPUTS_FILE].decode("utf-8")).get("inputs")         if vm_snapshot.PRODUCTION_INPUTS_FILE in reads else {}
    merged_inputs = dict(fresh_inputs if isinstance(fresh_inputs, dict) else {})
    for reference, item in previous_production_inputs(state_dir).items():  # a failed read must not drop a CO's check
        if reference in details and reference not in merged_inputs:
            merged_inputs[reference] = item
    for reference, item in previous_production_inputs(state_dir).items():  # ONLY a failed country read keeps the previous one
        if reference not in failed_countries:
            continue
        fresh = merged_inputs.get(reference)
        country = vm_snapshot.country_of(item.get("country")) if isinstance(item, dict) else None
        if isinstance(fresh, dict) and "country" not in fresh and country is not None and fresh.get("engine") == dashboard.CE_ENGINE:
            merged_inputs[reference] = {**fresh, "country": country}
    reads[vm_snapshot.PRODUCTION_INPUTS_FILE] = json.dumps({"inputs": merged_inputs}, sort_keys=True).encode("utf-8")
    counts["production_inputs"] = len(merged_inputs)
    # The Salesforce reads above take minutes. Re-read what the current snapshot carries NOW, so records, approvals or a clone copy
    # pushed meanwhile are kept instead of overwritten by the copies read at the start (security review 2026-10-27).
    carried, _latest_details = carried_files(state_dir)
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
    parser.add_argument("--transport", choices=("rest", "cli"), default="rest",
                        help="rest: read-only service identity (--config); cli: an operator's in-memory SSO session (--session-dir).")
    parser.add_argument("--session-dir", default=os.environ.get(SESSION_DIR_ENV, ""),
                        help="cli only: the tmpfs folder holding the Salesforce CLI's home (under /run/ or /dev/shm/).")
    parser.add_argument("--config", default=os.environ.get(reader.CONFIG_ENV, ""), help="rest only: reader configuration JSON file.")
    parser.add_argument("--key-file", default="", help="Snapshot HMAC key file (root-owned, outside the repository).")
    parser.add_argument("--state-dir", default=os.environ.get("SURFACE_ONBOARDING_STATE_DIR", "") or DEFAULT_STATE_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Check the configuration and key files only.")
    args = parser.parse_args(argv)
    needs = args.config if args.transport == "rest" else args.session_dir
    if not needs or not args.key_file or not os.path.isabs(args.state_dir):
        print("usage: --key-file and (--config for rest, --session-dir for cli) are required; --state-dir must be absolute")
        return 64
    code: str | None = None
    exit_code = 0
    counts: dict[str, int] = {}
    try:
        if args.dry_run:
            vm_snapshot.load_key(args.key_file)
            if args.transport == "rest":
                config = reader.load_config(args.config)
                reader._private_file(Path(config.private_key_file), secret=True, reason="key_file_unsafe")
            else:
                problem = session_dir_problem(args.session_dir)
                if problem not in (None, "session_needed"):
                    raise RefreshRefused(problem)
            print("dry run: configuration and key files are acceptable; nothing was read or written")
            return 0
        handle = _lock(args.state_dir)
        try:
            counts = refresh(args.config, args.key_file, args.state_dir, transport=args.transport,
                             session_dir=args.session_dir)
        finally:
            handle.close()
    except RefreshRefused as refusal:
        code, exit_code = refusal.code, refusal.exit_code
    except (vm_snapshot.SnapshotError, reader.ReaderError) as error:
        code, exit_code = (getattr(error, "code", None) or getattr(error, "reason", "error")), 2
    except Exception:  # noqa: BLE001 - never print a traceback that could echo a path or value
        code, exit_code = "unexpected_error", 2
    if code is not None:
        if code != "refresh_already_running":  # a competing run owns the status
            write_status(args.state_dir, status_result(code, args.transport), args.transport)
        print("refused:" + code)
        return exit_code
    write_status(args.state_dir, "ok", args.transport)
    print(" ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    print("refreshed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
