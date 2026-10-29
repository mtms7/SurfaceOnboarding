"""VM-started onboarding run on the owner's PC (owner decision 2026-10-27, docs/62). Locked by default.

The VM page can ask the owner's PC to start the guarded production create for ONE Approved CO. Nothing here creates anything:

* VM side: ``request_remote_start`` writes one short-lived request file (5 minutes, one pending request, 3 per hour, 15 minutes
  per CO); ``deliver`` hands it out once to the PC's signed poll and marks it delivered.
* Transport: the PC polls over the SAME restricted forced-command SSH key as the clone push, with an HMAC-signed bundle of one file
  (producer ``desktop-remote-poll``). No new key, no new root program, nothing inbound to the PC.
* PC side: ``Inbox.accept`` refuses expired, replayed, over-long-lived and over-rate requests; the dashboard then runs the SAME
  ``create_ui.handle_create_post`` as the "Onboard in production" button (every gate), with the owner as operator. SSO and MFA stay
  a human step in the BackOffice window.

Stdlib only; no secret or credential is read or logged here (the HMAC key is passed as bytes by the caller).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from . import vm_snapshot as vs

REQUEST_FILE = "remote-start-request.json"
REQUEST_LOG = "remote-start-requests.jsonl"
INBOX_FILE = "remote-start-inbox.json"
RUN_LOG = "remote-start-log.jsonl"
REQUEST_TTL = timedelta(minutes=5)
LIFETIME_SKEW = timedelta(seconds=60)
SAME_CO_COOL_DOWN = timedelta(minutes=15)
MAX_PER_HOUR = 3
ENABLE_ENV = "SURFACE_ONBOARDING_REMOTE_START"  # exactly "1" on the PC dashboard process (and on the VM for the button)
OPERATOR_ENV = "SURFACE_ONBOARDING_REMOTE_START_OPERATOR"
PUSH_KEY_ENV = "SURFACE_REMOTE_START_PUSH_KEY"
HMAC_KEY_ENV = "SURFACE_REMOTE_START_HMAC_KEY"
OWNER_ENV = "SURFACE_ONBOARDING_REMOTE_START_OWNER"  # VM side: the ONE e-mail allowed to ask (the PC compares it again)
HOST_ENV = "SURFACE_REMOTE_START_VM_HOST"
USER_ENV = "SURFACE_REMOTE_START_VM_USER"
POLL_SECONDS = 20
# Set for the runner child of a REMOTE start only (the launcher forwards SURFACE_ONBOARDING_PRODUCTION_* names): the runner then uses a
# brand-new temporary browser profile, so BackOffice must ask for SSO and MFA (owner decision 2026-10-27, docs/62 G9).
FRESH_SIGNIN_ENV = "SURFACE_ONBOARDING_PRODUCTION_FRESH_SIGNIN"
_STAMP = "%Y-%m-%dT%H:%M:%SZ"
_REFERENCE = re.compile(r"CO-[0-9]{3,6}")
_OPERATOR = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,80}")
_NONCE = re.compile(r"[0-9a-f]{32}")
_REVISION = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{4})")
_REPLY = re.compile(r"remote-start (CO-[0-9]{3,6}) ([0-9a-f]{32}) ([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) "
                    r"([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,80}) ([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+Z-]{8,32})")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,79}")
_SSH_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_STAMP)


def _parse(value: object) -> datetime | None:
    try:
        return datetime.strptime(value, _STAMP).replace(tzinfo=timezone.utc)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _write_json(path: Path, data: object) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


# ----- VM side -------------------------------------------------------------------------------------------------------------

def _read_request(state_dir: Path) -> dict | None:
    try:
        raw = json.loads((state_dir / REQUEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(raw, dict) or not isinstance(raw.get("co_reference"), str) or not _REFERENCE.fullmatch(raw["co_reference"])
            or not isinstance(raw.get("nonce"), str) or not _NONCE.fullmatch(raw["nonce"])
            or _parse(raw.get("expires_at")) is None or _parse(raw.get("requested_at")) is None
            or not isinstance(raw.get("requested_by"), str) or not _OPERATOR.fullmatch(raw["requested_by"])
            or not isinstance(raw.get("revision"), str) or not _REVISION.fullmatch(raw["revision"])):
        return None
    return raw


MAX_LOG_LINES = 400
KEEP_LOG_LINES = 200


def _trim(path: Path) -> None:
    """Keep a words-only log bounded: past MAX_LOG_LINES only the newest KEEP_LOG_LINES stay."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LOG_LINES:
            _write_json_lines(path, lines[-KEEP_LOG_LINES:])
    except OSError:
        pass


def _write_json_lines(path: Path, lines: list) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _log(state_dir: Path, co: object, operator: object, now: datetime, result: str) -> bool:
    """One line of words per attempt (CO, requester e-mail, time, result): never a nonce, a UUID or a credential. True when written."""
    try:
        line = json.dumps({"co_reference": co if isinstance(co, str) and _REFERENCE.fullmatch(co) else "invalid",
                           "requested_by": operator if isinstance(operator, str) and _OPERATOR.fullmatch(operator) else "invalid",
                           "requested_at": _stamp(now), "result": result}, sort_keys=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        with open(state_dir / REQUEST_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _trim(state_dir / REQUEST_LOG)
        return True
    except OSError:
        return False


def _recent_requests(state_dir: Path, now: datetime) -> list[tuple[str, datetime]]:
    found: list[tuple[str, datetime]] = []
    try:
        lines = (state_dir / REQUEST_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        moment = _parse(item.get("requested_at")) if isinstance(item, dict) else None
        if moment is not None and item.get("result") == "requested" and now - moment < timedelta(hours=1):
            found.append((str(item.get("co_reference")), moment))
    return found


def request_remote_start(state_dir: str | os.PathLike[str], co_reference: object, operator: object,
                         now: datetime, revision: object = None) -> tuple[str, dict | None]:
    """``("requested", request)`` or a refusal code. Writes nothing but the request file and one log line.

    ``revision`` is the Salesforce LastModifiedDate of the CO as the owner saw it in the published snapshot; the PC refuses to start
    when its live row differs (the owner approves what they saw). The request is also refused when the log cannot be written, because
    the rate limit is counted from that log."""
    folder = Path(state_dir)
    if not isinstance(co_reference, str) or not _REFERENCE.fullmatch(co_reference):
        return "bad_reference", None
    if not isinstance(operator, str) or not _OPERATOR.fullmatch(operator):
        return "bad_operator", None
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        return "bad_revision", None
    pending = _read_request(folder)
    if pending is not None and pending.get("delivered_at") is None and _parse(pending["expires_at"]) > now:
        _log(folder, co_reference, operator, now, "request_pending")
        return "request_pending", None
    recent = _recent_requests(folder, now)
    if any(co == co_reference and now - moment < SAME_CO_COOL_DOWN for co, moment in recent):
        _log(folder, co_reference, operator, now, "too_soon")
        return "too_soon", None
    if len(recent) >= MAX_PER_HOUR:
        _log(folder, co_reference, operator, now, "rate_limited")
        return "rate_limited", None
    request = {"co_reference": co_reference, "requested_by": operator, "requested_at": _stamp(now),
               "expires_at": _stamp(now + REQUEST_TTL), "nonce": secrets.token_hex(16), "delivered_at": None, "revision": revision}
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _write_json(folder / REQUEST_FILE, request)
    except OSError:
        return "store_unavailable", None
    if not _log(folder, co_reference, operator, now, "requested"):
        try:
            (folder / REQUEST_FILE).unlink()
        except OSError:
            pass
        return "store_unavailable", None
    return "requested", request


def deliver(state_dir: str | os.PathLike[str], now: datetime) -> str:
    """The one-line reply for a PC poll: ``remote-start CO-x <nonce> <expires>`` once per request, else ``none``."""
    folder = Path(state_dir)
    request = _read_request(folder)
    if request is None or request.get("delivered_at") is not None or _parse(request["expires_at"]) <= now:
        return "none"
    request["delivered_at"] = _stamp(now)
    try:
        _write_json(folder / REQUEST_FILE, request)
    except OSError:
        return "none"  # never hand out a request that could not be marked delivered
    return f"remote-start {request['co_reference']} {request['nonce']} {request['expires_at']} {request['requested_by']} {request['revision']}"


def build_poll_bundle(key: bytes, now: datetime) -> bytes:
    return vs.build_bundle({vs.REMOTE_POLL_FILE: json.dumps({"poll": 1}).encode("utf-8")}, key, vs.REMOTE_POLL_PRODUCER, now)


# ----- PC side -------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoteRequest:
    co_reference: str
    nonce: str
    expires_at: datetime
    requested_by: str
    revision: str


def parse_reply(line: object) -> RemoteRequest | None:
    if not isinstance(line, str):
        return None
    match = _REPLY.fullmatch(line.strip())
    expires = _parse(match.group(3)) if match else None
    if not match or expires is None or not _REVISION.fullmatch(match.group(5)):
        return None
    return RemoteRequest(match.group(1), match.group(2), expires, match.group(4), match.group(5))


class Inbox:
    """Nonces already accepted on this PC (survives a dashboard restart). Words and times only."""

    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.path = Path(state_dir) / INBOX_FILE

    def _load(self) -> list[dict] | None:
        """The accepted nonces; ``[]`` when there is no file yet; ``None`` when the file exists but cannot be trusted (fail closed)."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            return None
        try:
            raw = json.loads(text)["seen"]
        except (ValueError, KeyError, TypeError):
            return None
        if not isinstance(raw, list):
            return None
        return [item for item in raw if isinstance(item, dict) and isinstance(item.get("nonce"), str)]

    def accept(self, request: RemoteRequest, now: datetime) -> str | None:
        """``None`` = accepted and recorded; otherwise the refusal code. Nothing is recorded on a refusal."""
        if request.expires_at <= now:
            return "expired"
        if request.expires_at > now + REQUEST_TTL + LIFETIME_SKEW:
            return "bad_lifetime"
        seen = self._load()
        if seen is None:
            return "inbox_unreadable"
        if any(item["nonce"] == request.nonce for item in seen):
            return "replay"
        recent = [item for item in seen if (_parse(item.get("accepted_at")) or now) > now - timedelta(hours=1)]
        if len(recent) >= MAX_PER_HOUR:
            return "rate_limited"
        seen.append({"nonce": request.nonce, "co_reference": request.co_reference, "accepted_at": _stamp(now)})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _write_json(self.path, {"seen": seen[-100:]})
        except OSError:
            return "store_unavailable"
        return None


@dataclass(frozen=True)
class PcConfig:
    operator: str
    host: str
    user: str
    push_key: str
    hmac_key: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "PcConfig | None":
        """``None`` (locked) unless every setting is present and valid. The enable switch is exactly ``1``."""
        if env.get(ENABLE_ENV) != "1":
            return None
        operator, host = env.get(OPERATOR_ENV, ""), env.get(HOST_ENV, "")
        user = env.get(USER_ENV, "workato")
        push_key, hmac_key = env.get(PUSH_KEY_ENV, ""), env.get(HMAC_KEY_ENV, "")
        if (not _OPERATOR.fullmatch(operator) or not _HOST.fullmatch(host) or not _SSH_USER.fullmatch(user)
                or not push_key or not hmac_key or not Path(push_key).is_file() or not Path(hmac_key).is_file()):
            return None
        return cls(operator, host, user, push_key, hmac_key)


SshRunner = Callable[[list, bytes], tuple]


def poll_once(config: PcConfig, ssh: SshRunner, now: datetime) -> RemoteRequest | None:
    """One poll of the VM over the restricted forced-command key. ``None`` for no request, any error or any unexpected reply."""
    try:
        key = vs.load_key(config.hmac_key)
        argv = ["ssh", "-T", "-i", config.push_key, "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=15", f"{config.user}@{config.host}", "clone-push"]
        code, out = ssh(argv, build_poll_bundle(key, now))
    except (OSError, vs.SnapshotError, ValueError):  # the runner (tools/, the dashboard) turns process errors into OSError
        return None
    if code != 0:
        return None
    lines = out.splitlines()
    return parse_reply(lines[0] if lines else "")


_WORD = re.compile(r"[a-z0-9_]{1,60}")


def _run_log(state_dir: str | os.PathLike[str], co: str, word: str, now: datetime) -> None:
    """One line of words per tick that saw a request: never a nonce, a path or a credential."""
    try:
        folder = Path(state_dir)
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / RUN_LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"co_reference": co, "result": word, "at": _stamp(now)}, sort_keys=True) + "\n")
        _trim(folder / RUN_LOG)
    except OSError:
        pass


def process(config: PcConfig, inbox: Inbox, ssh: SshRunner, now: datetime, start: Callable[[str, str, str], str],
            state_dir: str | os.PathLike[str]) -> str:
    """One PC tick: poll the VM, accept the request (expiry, replay, rate), start the run, log the result word.

    ``start(co_reference, operator, revision)`` is the dashboard's own create path (the same gates as the button) and returns its code.
    Returns ``idle`` when there was no request, else the refusal or result word."""
    request = poll_once(config, ssh, now)
    if request is None:
        return "idle"
    if request.requested_by.casefold() != config.operator.casefold():  # only the configured owner may ask (the VM checks it too)
        _run_log(state_dir, request.co_reference, "wrong_requester", now)
        return "wrong_requester"
    refusal = inbox.accept(request, now)
    if refusal is not None:
        _run_log(state_dir, request.co_reference, refusal, now)
        return refusal
    try:
        word = start(request.co_reference, config.operator, request.revision)
    except Exception:  # noqa: BLE001 - a crashing start must never kill the poller or leak a traceback
        word = "start_failed"
    word = word if isinstance(word, str) and _WORD.fullmatch(word) else "unexpected_result"
    _run_log(state_dir, request.co_reference, word, now)
    return word


def start_worker(config: PcConfig | None, state_dir: str | os.PathLike[str], start: Callable[[str, str, str], str],
                 ssh: SshRunner | None = None, interval: float = POLL_SECONDS) -> threading.Thread | None:
    """The PC dashboard's background poller. ``None`` (nothing starts) unless the configuration is complete and enabled."""
    if config is None or ssh is None:  # the ssh runner comes from the dashboard (tools/): no process execution here
        return None
    inbox = Inbox(state_dir)

    def loop() -> None:
        while True:
            try:
                process(config, inbox, ssh, datetime.now(timezone.utc), start, state_dir)
            except Exception:  # noqa: BLE001 - keep polling
                pass
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="remote-start-poller", daemon=True)
    thread.start()
    return thread
