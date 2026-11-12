"""Local dashboard accounts (owner request 2026-11-12, phase 1b).

Owner decisions 2026-11-12: the dashboard signs people in with a local username and password (no dashboard MFA; MFA
stays on Salesforce and BackOffice). Exactly one super admin (``mmachados``), created only from the setup command, never
from the page. The super admin creates operators and viewers with a temporary password that must be changed at first
sign-in. Passwords are stored only as salted scrypt hashes inside the encrypted store (see secure_store.py).

Pure rules here; the dashboard and the setup command call them and persist through SecureStore.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from typing import Any

ROLE_SUPERADMIN = "superadmin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_SUPERADMIN, ROLE_OPERATOR, ROLE_VIEWER)
ASSIGNABLE_ROLES = (ROLE_OPERATOR, ROLE_VIEWER)
USERNAME = re.compile(r"[a-z][a-z0-9._-]{2,31}")
MIN_PASSWORD = 12
MAX_PASSWORD = 128
MAX_FAILURES = 5
LOCK_SECONDS = 15 * 60
MAX_ACCOUNTS = 100
SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_LEN = 2 ** 14, 8, 1, 32
_DUMMY_HASH: str | None = None


class AccountError(ValueError):
    """A refused account operation. The message is a short reason code, never a password."""


def may_operate(role: str | None) -> bool:
    return role in (ROLE_SUPERADMIN, ROLE_OPERATOR)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN,
                            maxmem=64 * 1024 * 1024)
    return "scrypt${}${}${}${}${}".format(SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(),
                                          base64.b64encode(digest).decode())


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected), maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def password_problem(password: str, username: str) -> str | None:
    """None when acceptable, else a reason code."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return "password_too_short"
    if len(password) > MAX_PASSWORD:
        return "password_too_long"
    if password.strip() != password:
        return "password_edge_spaces"
    if username and username.casefold() in password.casefold():
        return "password_contains_username"
    return None


def normalise_username(username: str) -> str:
    value = (username or "").strip().lower()
    if not USERNAME.fullmatch(value):
        raise AccountError("invalid_username")
    return value


def _now() -> float:
    return time.time()


def _new_account(role: str, password: str, *, must_change: bool, now: float) -> dict[str, Any]:
    return {"role": role, "hash": hash_password(password), "must_change": must_change, "disabled": False,
            "failures": 0, "locked_until": 0.0, "created": now, "changed": now}


def superadmin_name(document: dict[str, Any]) -> str | None:
    for name, account in document.get("accounts", {}).items():
        if account.get("role") == ROLE_SUPERADMIN:
            return name
    return None


def create_superadmin(document: dict[str, Any], username: str, password: str, *, now: float | None = None) -> str:
    """Only the setup command calls this. Refuses when a super admin already exists."""
    if superadmin_name(document) is not None:
        raise AccountError("superadmin_exists")
    name = normalise_username(username)
    problem = password_problem(password, name)
    if problem:
        raise AccountError(problem)
    if name in document["accounts"]:
        raise AccountError("already_exists")
    document["accounts"][name] = _new_account(ROLE_SUPERADMIN, password, must_change=False, now=now or _now())
    return name


def add_account(document: dict[str, Any], username: str, role: str, temporary_password: str,
                *, now: float | None = None) -> str:
    name = normalise_username(username)
    if name in document["accounts"]:
        raise AccountError("already_exists")
    if role not in ASSIGNABLE_ROLES:
        raise AccountError("invalid_role")
    if len(document["accounts"]) >= MAX_ACCOUNTS:
        raise AccountError("too_many_accounts")
    problem = password_problem(temporary_password, name)
    if problem:
        raise AccountError(problem)
    document["accounts"][name] = _new_account(role, temporary_password, must_change=True, now=now or _now())
    return name


def _managed(document: dict[str, Any], username: str) -> tuple[str, dict[str, Any]]:
    name = normalise_username(username)
    account = document["accounts"].get(name)
    if account is None:
        raise AccountError("not_found")
    if account.get("role") == ROLE_SUPERADMIN:
        raise AccountError("superadmin_protected")
    return name, account


def set_role(document: dict[str, Any], username: str, role: str, *, now: float | None = None) -> str:
    name, account = _managed(document, username)
    if role not in ASSIGNABLE_ROLES:
        raise AccountError("invalid_role")
    if account["role"] == role:
        raise AccountError("no_change")
    account["role"], account["changed"] = role, now or _now()
    return name


def reset_password(document: dict[str, Any], username: str, temporary_password: str, *, now: float | None = None) -> str:
    name, account = _managed(document, username)
    problem = password_problem(temporary_password, name)
    if problem:
        raise AccountError(problem)
    account.update(hash=hash_password(temporary_password), must_change=True, failures=0, locked_until=0.0,
                   changed=now or _now())
    return name


def set_disabled(document: dict[str, Any], username: str, disabled: bool, *, now: float | None = None) -> str:
    name, account = _managed(document, username)
    if bool(account.get("disabled")) == disabled:
        raise AccountError("no_change")
    account["disabled"], account["changed"] = disabled, now or _now()
    return name


def remove_account(document: dict[str, Any], username: str) -> str:
    name, _account = _managed(document, username)
    del document["accounts"][name]
    return name


def change_own_password(document: dict[str, Any], username: str, current: str, new: str,
                        *, now: float | None = None) -> str:
    name = normalise_username(username)
    account = document["accounts"].get(name)
    if account is None or not check_password(current, account["hash"]):
        raise AccountError("current_password_wrong")
    problem = password_problem(new, name)
    if problem:
        raise AccountError(problem)
    if check_password(new, account["hash"]):
        raise AccountError("password_unchanged")
    account.update(hash=hash_password(new), must_change=False, changed=now or _now())
    return name


def authenticate(document: dict[str, Any], username: str, password: str,
                 *, now: float | None = None) -> tuple[str | None, str]:
    """(username or None, reason). Updates the failure counter and lock in ``document`` (the caller saves it).

    Reasons: ok, invalid (unknown user or wrong password; deliberately the same), locked, disabled.
    """
    global _DUMMY_HASH
    now = now or _now()
    try:
        name = normalise_username(username)
    except AccountError:
        name = ""
    account = document["accounts"].get(name) if name else None
    if account is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
        check_password(password or "", _DUMMY_HASH)  # same work as a real check
        return None, "invalid"
    if account.get("locked_until", 0) > now:
        return None, "locked"
    if not check_password(password or "", account["hash"]):
        account["failures"] = int(account.get("failures", 0)) + 1
        if account["failures"] >= MAX_FAILURES:
            account["failures"], account["locked_until"] = 0, now + LOCK_SECONDS
            return None, "locked"
        return None, "invalid"
    if account.get("disabled"):
        return None, "disabled"
    account["failures"], account["locked_until"] = 0, 0.0
    return name, "ok"


def public_view(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for the Users page: never the hash."""
    rows = []
    for name, account in sorted(document.get("accounts", {}).items(),
                                key=lambda item: (ROLES.index(item[1].get("role", ROLE_VIEWER)), item[0])):
        rows.append({"username": name, "role": account.get("role"), "must_change": bool(account.get("must_change")),
                     "disabled": bool(account.get("disabled")), "locked": account.get("locked_until", 0) > _now(),
                     "changed": account.get("changed", 0)})
    return rows
