"""Single users file for the VM team-access mode (docs/40): one source of truth for the allow-list and roles.

Format, one entry per line (``#`` starts a comment, blank lines ignored)::

    person@example.com             viewer (read-only)
    person@example.com operator    operator (also allow-listed)

The dashboard reads this file; ``integration/deployment/vm_pilot/render_allowed_emails.py`` renders oauth2-proxy's
plain e-mail list from it. Fail closed: any malformed line makes the whole file invalid (no user is admitted).
Errors carry a line number and a reason only, never the line text.
"""

from __future__ import annotations

import re
from pathlib import Path

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"
EMAIL_PATTERN = re.compile(r"[a-z0-9._%+'-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,24}")
MAX_USERS_FILE_BYTES = 65536


class UsersFileError(ValueError):
    """The users file is malformed. The message never contains file content."""


def parse_users(text: str) -> dict[str, str]:
    """``{lower-cased e-mail: role}``. Raises UsersFileError on the first malformed line."""
    users: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) > 2:
            raise UsersFileError(f"line {number}: expected 'email' or 'email operator'")
        email = parts[0].lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise UsersFileError(f"line {number}: not a valid e-mail address")
        role = ROLE_VIEWER
        if len(parts) == 2:
            if parts[1].lower() != ROLE_OPERATOR:
                raise UsersFileError(f"line {number}: the only optional second field is 'operator'")
            role = ROLE_OPERATOR
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
