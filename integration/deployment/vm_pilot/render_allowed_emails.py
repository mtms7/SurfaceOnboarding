#!/usr/bin/env python3
"""REVIEW ARTIFACT - not installed; needs approvals per docs/40 section 7.

Render oauth2-proxy's ``authenticated_emails_file`` (plain e-mail list) from the single users file, so the allow-list
has one source of truth:

    python3 render_allowed_emails.py /etc/surface-onboarding/users.txt /etc/oauth2-proxy/allowed-emails.txt

Output: lower-cased, de-duplicated, sorted, one e-mail per line, nothing else. The result is written atomically and
only when it changed (idempotent). A malformed users file exits non-zero and leaves the old output untouched.
Prints only a count or an error with a line number, never e-mail addresses. Standard library plus the repository's
users_file parser; it makes no network calls.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from integration.onboarding.users_file import UsersFileError, allowed_emails, load_users_file  # noqa: E402


def render(users_path: str | Path) -> str:
    emails = allowed_emails(load_users_file(users_path))
    if not emails:
        raise UsersFileError("users file lists nobody")
    return "".join(email + "\n" for email in emails)


def write_if_changed(output_path: str | Path, content: str) -> bool:
    """Atomically replace the output when its content differs. Returns True when it was written."""
    target = Path(output_path)
    try:
        if target.read_text(encoding="utf-8") == content:
            return False
    except (OSError, UnicodeError):
        pass
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        try:
            os.chmod(temp_name, target.stat().st_mode & 0o7777 if target.exists() else 0o640)
        except OSError:
            pass
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: render_allowed_emails.py USERS_FILE OUTPUT_FILE", file=sys.stderr)
        return 2
    try:
        content = render(argv[1])
        changed = write_if_changed(argv[2], content)
    except UsersFileError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"refused: cannot write the output file ({type(error).__name__})", file=sys.stderr)
        return 1
    print(f"{'written' if changed else 'unchanged'}: {content.count(chr(10))} address(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
