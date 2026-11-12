"""Single users file for the dashboard (docs/40; desktop roles 2026-11-12): one source of truth for the allow-list and roles.

Format, one entry per line (``#`` starts a comment, blank lines ignored)::

    person@example.com             viewer (read-only)
    person@example.com operator    operator (also allow-listed)
    person@example.com admin       admin: operator rights plus the Users page (desktop)

The dashboard reads this file; ``integration/deployment/vm_pilot/render_allowed_emails.py`` renders oauth2-proxy's
plain e-mail list from it. Fail closed: any malformed line makes the whole file invalid (no user is admitted).
Errors carry a line number and a reason only, never the line text.

The desktop Users page (admin only) changes the file through ``apply_change`` + ``save_users_file`` and records every
change with ``append_change`` (append-only JSON lines next to the file: when, by whom, what). The last admin can never be
removed or demoted.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)
EMAIL_PATTERN = re.compile(r"[a-z0-9._%+'-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}")
MAX_USERS_FILE_BYTES = 65536
MAX_USERS = 200
CHANGE_ACTIONS = ("add", "set", "remove")
FILE_HEADER = ("# Surface onboarding dashboard users: one 'email role' per line (role: admin, operator or viewer).\n"
               "# Managed from the dashboard's Users page (admin only); every change is logged next to this file.\n")


class UsersFileError(ValueError):
    """The users file is malformed. The message never contains file content."""


class UserChangeError(ValueError):
    """A requested change is refused. The message is a short reason code."""


def parse_users(text: str) -> dict[str, str]:
    """``{lower-cased e-mail: role}``. Raises UsersFileError on the first malformed line."""
    users: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) > 2:
            raise UsersFileError(f"line {number}: expected 'email' or 'email role'")
        email = parts[0].lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise UsersFileError(f"line {number}: not a valid e-mail address")
        role = ROLE_VIEWER
        if len(parts) == 2:
            role = parts[1].lower()
            if role not in (ROLE_OPERATOR, ROLE_ADMIN):
                raise UsersFileError(f"line {number}: the only optional second field is 'operator' or 'admin'")
        if users.get(email, role) != role:
            raise UsersFileError(f"line {number}: the same e-mail is listed with two different roles")
        users[email] = role
    return users


def load_users_file(path: str | Path) -> dict[str, str]:
    """Parse a users file. Raises UsersFileError (malformed/unreadable/too large)."""
    try:
        target = Path(path)
        if target.stat().st_size > MAX_USERS_FILE_BYTES:
            raise UsersFileError("users file is too large")
        return parse_users(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise UsersFileError("users file is unreadable") from error


def allowed_emails(users: dict[str, str]) -> list[str]:
    """Sorted unique e-mails for oauth2-proxy's ``authenticated_emails_file``."""
    return sorted(users)


def may_operate(role: str | None) -> bool:
    """Operators and admins may start runs and actions; viewers may only read."""
    return role in (ROLE_OPERATOR, ROLE_ADMIN)


def format_users(users: dict[str, str]) -> str:
    """The file text: header, then admins, operators, viewers, each sorted by e-mail."""
    order = {role: index for index, role in enumerate(ROLES)}
    lines = [email + ("" if role == ROLE_VIEWER else " " + role)
             for email, role in sorted(users.items(), key=lambda item: (order[item[1]], item[0]))]
    return FILE_HEADER + "".join(line + "\n" for line in lines)


def apply_change(users: dict[str, str], action: str, email: str, role: str | None = None) -> dict[str, str]:
    """A new ``{email: role}`` with one change applied, or UserChangeError (reason code). ``users`` is not modified."""
    email = (email or "").strip().lower()
    if action not in CHANGE_ACTIONS:
        raise UserChangeError("unknown_action")
    if not EMAIL_PATTERN.fullmatch(email):
        raise UserChangeError("invalid_email")
    updated = dict(users)
    if action == "add":
        if email in users:
            raise UserChangeError("already_listed")
        if len(users) >= MAX_USERS:
            raise UserChangeError("too_many_users")
        if role not in ROLES:
            raise UserChangeError("invalid_role")
        updated[email] = role
    elif action == "set":
        if email not in users:
            raise UserChangeError("not_listed")
        if role not in ROLES:
            raise UserChangeError("invalid_role")
        if users[email] == role:
            raise UserChangeError("no_change")
        updated[email] = role
    else:
        if email not in users:
            raise UserChangeError("not_listed")
        del updated[email]
    if not any(value == ROLE_ADMIN for value in updated.values()):
        raise UserChangeError("last_admin")
    return updated


def save_users_file(path: str | Path, users: dict[str, str]) -> None:
    """Write the file atomically (temporary file in the same folder, then replace). Validates before writing."""
    text = format_users(users)
    if parse_users(text) != users:
        raise UsersFileError("users do not round-trip")
    target = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".users-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def change_log_path(path: str | Path) -> Path:
    """The append-only change log that sits next to the users file."""
    target = Path(path)
    return target.with_name(target.name + ".changes.jsonl")


def append_change(path: str | Path, by: str, action: str, email: str, role: str | None,
                  *, now: datetime | None = None) -> dict[str, str]:
    """Append one change record (UTC time, admin, action, e-mail, role) and return it."""
    entry = {"at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by, "action": action,
             "email": email.strip().lower(), "role": role or ""}
    with change_log_path(path).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def recent_changes(path: str | Path, limit: int = 20) -> list[dict[str, str]]:
    """The newest ``limit`` change records, newest first; unreadable or malformed lines are skipped."""
    try:
        lines = change_log_path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    entries: list[dict[str, str]] = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and all(isinstance(entry.get(key), str) for key in ("at", "by", "action", "email")):
            entries.append(entry)
        if len(entries) >= limit:
            break
    return entries
