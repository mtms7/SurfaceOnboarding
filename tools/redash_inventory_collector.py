"""Read-only collector: Redash saved query 251 (production clone) -> local tenant inventory snapshot.

Owner approval 2026-10-05: read-only, automated use of one saved query on the
Redash data source "Prod (Cloned) - Mgmt". It needs no browser and no MFA, so
it can run unattended (Windows Task Scheduler on the operator desktop first,
a systemd timer on the VM later).

Safety properties (each is tested):
* Only host ``redash.pentera.io`` over HTTPS, and only four exact (method, path)
  pairs: this query's cached results (GET), its refresh (POST), one job's status
  (GET) and one result by number (GET). Redirects are refused so the key never
  leaves that host. The key travels in the ``Authorization`` header, never in a URL.
* The per-query API key is stored only as a Windows DPAPI blob (current user)
  under %LOCALAPPDATA%\\SurfaceOnboarding\\redash. It is entered with a hidden
  prompt on a real console, never taken from an argument, and never printed,
  logged, or put in an exception message.
* Output is reason codes and counts only, never row values.
* Every failure (auth, WAF, wrong shape, edited query, shrunken result, stale or
  future data, too large) fails closed and leaves the previous snapshot untouched.
* A refresh that did not happen is visible: the run exits 2 (a scheduled-task
  failure), not 0, while the older data is still written with its true age.
* The snapshot is informational: it can block a duplicate, it never clears one.

Usage:
    python tools/redash_inventory_collector.py --store-key   # once, in your own terminal
    python tools/redash_inventory_collector.py --probe       # shape only: column names, types, counts
    python tools/redash_inventory_collector.py --collect     # what the scheduled task runs
Exit codes: 0 ok, 1 failed (nothing written), 2 written but the refresh did not happen.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Any, Callable
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.onboarding import leonardo_inventory as inventory  # noqa: E402
from integration.onboarding import redash_inventory as redash  # noqa: E402

ENVIRONMENT = "prod-clone"
BASE_URL = inventory.ENVIRONMENTS[ENVIRONMENT].origin
REDASH_HOST = "redash.pentera.io"
assert urlsplit(BASE_URL).scheme == "https" and urlsplit(BASE_URL).hostname == REDASH_HOST
QUERY_ID = redash.QUERY_ID
RESULTS_PATH = f"/api/queries/{QUERY_ID}/results.json"
REFRESH_PATH = f"/api/queries/{QUERY_ID}/results"
ALLOWED_CALLS = (
    ("GET", re.compile(rf"/api/queries/{QUERY_ID}/results\.json")),
    ("POST", re.compile(rf"/api/queries/{QUERY_ID}/results")),
    ("GET", re.compile(r"/api/jobs/[A-Za-z0-9-]{8,64}")),
    ("GET", re.compile(r"/api/query_results/[0-9]{1,12}\.json")),
)
KEY_PATTERN = re.compile(r"[A-Za-z0-9]{20,64}")
JOB_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
DEFAULT_MAX_AGE = timedelta(minutes=60)   # older cached results trigger a refresh
HARD_MAX_AGE = timedelta(hours=36)        # older than this is refused, never snapshotted
FUTURE_SKEW = timedelta(minutes=5)        # a result "from the future" is refused
SHRINK_FLOOR = 0.9                        # a result below 90% of the last snapshot is refused
MAX_UUID_MISSING_SHARE = 0.10             # more tenants without a UUID than this means a broken query
REQUEST_TIMEOUT = 60
JOB_DEADLINE_SECONDS = 240                # overall, including slow requests
MAX_POLLS = 120
MAX_BYTES = 64 * 1024 * 1024
KEEP_SNAPSHOTS = 4                        # ~16 MB each for the production clone
_ENTROPY = b"surface-onboarding-redash-v1"
_USER_AGENT = "SurfaceOnboardingCollector/1"
_FINISHED, _FAILED, _CANCELED = 3, 4, 5   # Redash job states


class CollectorError(Exception):
    """Fail-closed outcome; ``reason`` is a stable, value-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ----- key storage (Windows DPAPI, current user) ---------------------------------

def state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Local") / "SurfaceOnboarding" / "redash"


def key_path() -> Path:
    return state_dir() / f"query-{QUERY_ID}.key"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise CollectorError("redash_key_store_unsupported")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    blob_pointer = ctypes.POINTER(_Blob)
    crypt32.CryptProtectData.argtypes = [blob_pointer, wintypes.LPCWSTR, blob_pointer, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    crypt32.CryptUnprotectData.argtypes = [blob_pointer, ctypes.c_void_p, blob_pointer, ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD, blob_pointer]
    crypt32.CryptProtectData.restype = crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    data_buffer = ctypes.create_string_buffer(data, len(data))
    entropy_buffer = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    source = _Blob(len(data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_char)))
    entropy = _Blob(len(_ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    target = _Blob()
    ui_forbidden = 0x1
    try:
        if protect:
            done = crypt32.CryptProtectData(ctypes.byref(source), "surface-onboarding-redash", ctypes.byref(entropy),
                                            None, None, ui_forbidden, ctypes.byref(target))
        else:
            done = crypt32.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                                              ui_forbidden, ctypes.byref(target))
        if not done:
            raise CollectorError("redash_key_store_failed" if protect else "redash_key_unavailable")
        try:
            return ctypes.string_at(target.pbData, target.cbData)
        finally:
            ctypes.memset(target.pbData, 0, target.cbData)  # wipe the Windows-allocated copy
            kernel32.LocalFree(target.pbData)
    finally:
        ctypes.memset(data_buffer, 0, len(data))  # wipe our copy of the input


def store_key(raw_key: str, path: Path | None = None) -> None:
    key = raw_key.strip()
    if not KEY_PATTERN.fullmatch(key):
        raise CollectorError("redash_key_format_invalid")
    target = path or key_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    blob = _dpapi(key.encode("ascii"), protect=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(blob)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def load_key(path: Path | None = None) -> str:
    try:
        blob = (path or key_path()).read_bytes()
    except OSError:
        raise CollectorError("redash_key_unavailable") from None
    key = _dpapi(blob, protect=False).decode("ascii", "replace")
    if not KEY_PATTERN.fullmatch(key):
        raise CollectorError("redash_key_unavailable")
    return key


def read_key_hidden() -> str:
    """Prompt for the key with echo off, and refuse anywhere a hidden prompt is not guaranteed."""
    if not (sys.stdin and sys.stdin.isatty()):
        raise CollectorError("redash_key_prompt_not_a_console")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass("Paste the Redash query API key (input is hidden): ")
        except getpass.GetPassWarning:
            raise CollectorError("redash_key_prompt_not_hidden") from None


# ----- transport ------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # the key must never follow a redirect
        return None


def build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect)


def _call(opener: Any, key: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    if not any(method == allowed and pattern.fullmatch(path) for allowed, pattern in ALLOWED_CALLS):
        raise CollectorError("redash_path_not_allowed")
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": "Key " + key, "Accept": "application/json", "User-Agent": _USER_AGENT}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE_URL + path, data=payload, method=method, headers=headers)
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if 300 <= code < 400:
            raise CollectorError("redash_redirect_refused") from None
        if code in (401, 403):
            raise CollectorError("redash_auth_failed") from None
        if code == 404:
            raise CollectorError("redash_query_not_found") from None
        if code == 429:
            raise CollectorError("redash_rate_limited") from None
        raise CollectorError("redash_http_error") from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
        raise CollectorError("redash_unreachable") from None
    if len(raw) > MAX_BYTES:
        raise CollectorError("redash_response_too_large")
    try:
        return json.loads(raw)
    except ValueError:
        raise CollectorError("redash_bad_json") from None


def _retrieved_at(result: Any) -> datetime:
    value = result.get("query_result", {}).get("retrieved_at") if isinstance(result, dict) else None
    try:
        stamp = redash.parse_timestamp_ms(value)
        if stamp is None:
            raise inventory.InventoryError("redash_schema_unavailable")
        return datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
    except (inventory.InventoryError, OverflowError, OSError, ValueError):
        raise CollectorError("redash_schema_unavailable") from None


def _refresh(opener: Any, key: str, sleep: Callable[[float], None], clock: Callable[[], float] | None = None) -> Any:
    """Ask Redash to re-run the saved query now (a read on the clone), then wait for its result."""
    clock = clock or time.monotonic
    deadline = clock() + JOB_DEADLINE_SECONDS
    reply = _call(opener, key, "POST", REFRESH_PATH, {"max_age": 0})
    if isinstance(reply, dict) and isinstance(reply.get("query_result"), dict):
        return {"query_result": reply["query_result"]}
    job = reply.get("job") if isinstance(reply, dict) else None
    job_id = job.get("id") if isinstance(job, dict) else None
    if type(job_id) is not str or not JOB_ID.fullmatch(job_id):
        raise CollectorError("redash_refresh_unexpected")
    for _ in range(MAX_POLLS):
        status = _call(opener, key, "GET", f"/api/jobs/{job_id}")
        info = status.get("job") if isinstance(status, dict) else None
        state = info.get("status") if isinstance(info, dict) else None
        if state == _FINISHED:
            result_id = info.get("query_result_id")
            if type(result_id) is not int or not 1 <= result_id < 10 ** 12:
                raise CollectorError("redash_refresh_unexpected")
            return _call(opener, key, "GET", f"/api/query_results/{result_id}.json")
        if state in (_FAILED, _CANCELED):
            raise CollectorError("redash_refresh_failed")
        if clock() >= deadline:
            break
        sleep(2)
    raise CollectorError("redash_refresh_timeout")


def fetch_results(opener: Any, key: str, *, now: datetime, max_age: timedelta, refresh: bool,
                  sleep: Callable[[float], None] = time.sleep) -> tuple[Any, bool, bool]:
    """The newest result, refreshed first when older than ``max_age`` (if allowed).

    Returns (result, refreshed, refresh_failed). ``refresh_failed`` is True when a refresh was wanted
    (the cached result was too old) and the data is still older than ``max_age`` afterwards.
    """
    result = _call(opener, key, "GET", RESULTS_PATH)
    refreshed = False
    if refresh and now - _retrieved_at(result) > max_age:
        try:
            result, refreshed = _refresh(opener, key, sleep), True
        except CollectorError:
            pass  # fall back to the cached result; the run is flagged below and the age limits still apply
    retrieved = _retrieved_at(result)
    if retrieved - now > FUTURE_SKEW:
        raise CollectorError("redash_results_future")
    if now - retrieved > HARD_MAX_AGE:
        raise CollectorError("redash_results_stale")
    return result, refreshed, bool(refresh and now - retrieved > max_age)


# ----- collect / probe --------------------------------------------------------------

def collect(opener: Any, key: str, *, root: Path | None = None, now: datetime | None = None,
            max_age: timedelta = DEFAULT_MAX_AGE, refresh: bool = True, write_csv: bool = False,
            accept_shrink: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    root = root or inventory.default_root()
    result, refreshed, refresh_failed = fetch_results(opener, key, now=now, max_age=max_age, refresh=refresh,
                                                      sleep=sleep)
    normalized = redash.normalize(result)
    if normalized.unexpected_columns:
        raise CollectorError("redash_query_changed")  # someone edited query 251: stop, do not guess
    assembled = inventory.assemble_rows(normalized.rows)
    if assembled.uuid_missing_count > assembled.total_count * MAX_UUID_MISSING_SHARE:
        raise CollectorError("redash_uuid_missing")
    environment = inventory.require_environment(ENVIRONMENT)
    directory = inventory.env_dir(root, ENVIRONMENT)
    try:
        latest = json.loads((directory / inventory.LATEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        latest = {}
    previous = latest.get("row_count") if isinstance(latest, dict) else None
    if (type(previous) is int and previous > 0 and assembled.total_count < previous * SHRINK_FLOOR
            and not accept_shrink):
        raise CollectorError("redash_result_shrunk")
    payload = inventory.snapshot_payload(
        environment, assembled, normalized.retrieved_at, acquisition=inventory.REDASH_ACQUISITION,
        source=redash.source_meta(normalized, now, refreshed=refreshed), expected_paths=redash.EXPECTED_REDASH_PATHS)
    drift = payload["schema_drift"]
    summary = {"environment": environment.name, "environment_label": environment.label,
               "row_count": payload["row_count"], "deleted_count": payload["deleted_count"],
               "uuid_missing_count": payload["uuid_missing_count"], "refreshed": refreshed,
               "refresh_failed": refresh_failed,
               "data_age_minutes": int((now - normalized.retrieved_at).total_seconds() // 60),
               "schema_drift": {name: len(drift.get(name, [])) for name in ("unknown", "missing", "type_changed")},
               "missing_columns": len(normalized.missing_columns)}
    try:  # "unchanged" only when the stored snapshot still verifies (hash, name, counts), not just latest.json
        stored = inventory.load_latest(root, ENVIRONMENT, max_age=timedelta(days=3650), now=now)
    except inventory.InventoryError:
        stored = None
    if stored is not None and stored.get("rows_sha256") == payload["rows_sha256"] \
            and stored.get("captured_at") == payload["captured_at"]:
        return {"result": "inventory_unchanged", **summary}
    target = inventory.write_snapshot(payload, root, keep=KEEP_SNAPSHOTS)
    if write_csv:
        csv_target = target.with_suffix(".csv")
        temporary = csv_target.with_name(f"{csv_target.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(inventory.to_csv(payload), encoding="utf-8", newline="")
            os.replace(temporary, csv_target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"result": "inventory_collected", "file": target.name, **summary}


def probe(opener: Any, key: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Shape only: column names, value type names, counts, freshness. No row value is printed."""
    now = now or datetime.now(timezone.utc)
    result = _call(opener, key, "GET", RESULTS_PATH)
    data = result.get("query_result", {}).get("data", {}) if isinstance(result, dict) else {}
    columns = [column.get("name") for column in data.get("columns", []) if isinstance(column, dict)]
    rows = [row for row in data.get("rows", []) if isinstance(row, dict)]
    types = {name: sorted({type(row[name]).__name__ for row in rows if row.get(name) is not None}) or ["all-null"]
             for name in columns if type(name) is str and redash._SAFE_COLUMN.fullmatch(name)}
    report: dict[str, Any] = {"result": "probe", "row_count": len(rows), "columns": types,
                              "data_age_minutes": None, "normalize": "not_run"}
    try:
        report["data_age_minutes"] = int((now - _retrieved_at(result)).total_seconds() // 60)
        normalized = redash.normalize(result)
        inventory.assemble_rows(normalized.rows)
        report["normalize"] = "ok"
        report["unexpected_columns"] = list(normalized.unexpected_columns)
        report["missing_columns"] = list(normalized.missing_columns)
    except (inventory.InventoryError, CollectorError) as error:
        report["normalize"] = error.reason
    return report


# ----- command line -------------------------------------------------------------------

def _log_line(text: str) -> None:
    """One line per event, reason codes and counts only (trimmed so the file stays small)."""
    try:
        path = state_dir() / "collector.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])[-199:]
        lines.append(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + " " + text)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--store-key", action="store_true", help="Store the per-query API key (hidden prompt).")
    mode.add_argument("--probe", action="store_true", help="Read the cached result and print its shape only.")
    mode.add_argument("--collect", action="store_true", help="Refresh if needed and write the local snapshot.")
    parser.add_argument("--max-age-minutes", type=int, default=int(DEFAULT_MAX_AGE.total_seconds() // 60))
    parser.add_argument("--no-refresh", action="store_true", help="Never ask Redash to re-run the query.")
    parser.add_argument("--csv", action="store_true", help="Also write a CSV next to the snapshot.")
    parser.add_argument("--accept-shrink", action="store_true",
                        help="Accept a result more than 10%% smaller than the last snapshot (a deliberate clean-up).")
    args = parser.parse_args(argv)
    try:
        if args.store_key:
            store_key(read_key_hidden())
            print(json.dumps({"result": "key_stored"}))
            return 0
        key = load_key()
        opener = build_opener()
        if args.probe:
            print(json.dumps(probe(opener, key), sort_keys=True))
            return 0
        _log_line("collect started")
        report = collect(opener, key, max_age=timedelta(minutes=max(1, args.max_age_minutes)),
                         refresh=not args.no_refresh, write_csv=args.csv, accept_shrink=args.accept_shrink)
    except (CollectorError, inventory.InventoryError) as error:
        report = {"result": error.reason}
        _log_line(json.dumps(report))
        print(json.dumps(report))
        return 1
    except Exception:
        report = {"result": "redash_collector_unavailable"}
        _log_line(json.dumps(report))
        print(json.dumps(report))
        return 1
    _log_line(json.dumps(report, sort_keys=True))
    print(json.dumps(report, sort_keys=True))
    return 2 if report.get("refresh_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
