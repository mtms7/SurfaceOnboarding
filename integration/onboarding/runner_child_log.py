"""Redacted, fixed-enum log of a production runner child (2026-10-19). Local file only; no connection, no process.

The dashboard starts the production runner with stdout/stderr discarded, so a failed attempt left no trace. When the
dashboard names a log file (``SURFACE_ONBOARDING_RUNNER_LOG``, absolute), the runner replaces its own stdout/stderr with a
``SanitizingStream`` that writes, per line, only:

* result JSON reduced to allow-listed keys whose values are short codes or record names (never a UUID-shaped value);
* a fixed set of operator lines (the sign-in wait, the form-left-open warning);
* for stderr, ``traceback`` or the exception CLASS name only (never the message, a path, a URL or a value).

Everything else is ``WITHHELD``. Writing never raises: a log problem must not break a production run. The runner writes the
file itself, so the dashboard restarting mid-run cannot break a pipe.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

LOG_ENV = "SURFACE_ONBOARDING_RUNNER_LOG"
WITHHELD = "[line withheld]"
PRODUCTION_FLAGS = ("--production-create", "--production-dry-run")
_KEYS = ("result", "outcome", "reason", "refusal_reason", "record", "record_error", "production_write",
         "salesforce_writeback")
_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_UUID_LIKE = re.compile(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_OPERATOR_LINES = ("Sign in with SSO and MFA in the window titled app.pentera.io; waiting up to 15 minutes",
                   "FORM LEFT OPEN: close the Add Account window in the production browser yourself (Cancel).")
_EXCEPTION = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Exit|Interrupt|Refusal|Refused))\b")


def _safe(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    return isinstance(value, str) and bool(_CODE.fullmatch(value)) and not _UUID_LIKE.search(value)


def sanitize(stream: str, line: str) -> str | None:
    """The loggable form of one line, ``WITHHELD``, or None (nothing worth a line)."""
    text = line.strip()
    if not text:
        return None
    if stream == "stderr":
        if text.startswith("Traceback"):
            return "stderr: traceback"
        match = _EXCEPTION.match(text)
        return "stderr: " + match.group(1) if match else None
    if text.startswith("{"):
        try:
            body = json.loads(text)
        except ValueError:
            return WITHHELD
        if not isinstance(body, dict):
            return WITHHELD
        return json.dumps({k: body[k] for k in _KEYS if k in body and _safe(body[k])}, separators=(",", ":"))
    for known in _OPERATOR_LINES:
        if text.startswith(known):
            return known
    return WITHHELD


class SanitizingStream:
    """A write-only text stream for sys.stdout / sys.stderr that appends sanitized, time-stamped lines to ``path``."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, path: Path, name: str) -> None:
        self.path, self.name, self._buffer = Path(path), name, ""

    def write(self, text: str) -> int:
        try:
            self._buffer += str(text)
            *lines, self._buffer = self._buffer.split("\n")
            for line in lines:
                self._emit(line)
        except Exception:  # noqa: BLE001 - logging never breaks the run
            pass
        return len(text) if isinstance(text, str) else 0

    def _emit(self, line: str) -> None:
        clean = sanitize(self.name, line)
        if clean is None:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{stamp} {self.name} {clean}\n")
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        if self._buffer:
            line, self._buffer = self._buffer, ""
            self._emit(line)

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True


def install_from_env(environ: Mapping[str, str], argv: Sequence[str]) -> bool:
    """Replace sys.stdout/sys.stderr with sanitizing streams for a production run whose log file the dashboard named."""
    raw = str(environ.get(LOG_ENV, "")).strip()
    if not raw or not Path(raw).is_absolute() or not any(flag in argv for flag in PRODUCTION_FLAGS):
        return False
    path = Path(raw)
    sys.stdout = SanitizingStream(path, "stdout")  # type: ignore[assignment]
    sys.stderr = SanitizingStream(path, "stderr")  # type: ignore[assignment]
    return True
