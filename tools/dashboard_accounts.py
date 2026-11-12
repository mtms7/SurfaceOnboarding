"""Setup and checks for the dashboard's local accounts (owner request 2026-11-12, phase 1b).

    python tools/dashboard_accounts.py init-superadmin --username mmachados
    python tools/dashboard_accounts.py status
    python tools/dashboard_accounts.py verify-audit

init-superadmin asks for the password twice at a hidden prompt (nothing is echoed, logged or stored except a salted
scrypt hash inside the encrypted store). It refuses when a super admin already exists: there is only ever one.
The store folder and key default to the same places the launcher uses; pass --dir / --key to override (the VM keeps the
key under /etc/surface-onboarding/ readable only by the service user).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.onboarding import accounts  # noqa: E402


def default_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "SurfaceOnboarding" / "accounts"
    return Path("/var/lib/surface-onboarding/accounts")


def open_store(args: argparse.Namespace):
    from integration.onboarding import secure_store
    folder = Path(args.dir or os.environ.get("SURFACE_DASHBOARD_ACCOUNTS_DIR") or default_dir())
    key = args.key or os.environ.get("SURFACE_DASHBOARD_ACCOUNTS_KEY") or None
    return secure_store.SecureStore(folder, key)


def init_superadmin(args: argparse.Namespace, prompt=getpass.getpass) -> int:
    store = open_store(args)
    document = store.load_accounts()
    if accounts.superadmin_name(document) is not None:
        print("refused: a super admin already exists (there is only ever one).")
        return 1
    try:
        name = accounts.normalise_username(args.username)
    except accounts.AccountError as error:
        print("refused: " + str(error))
        return 1
    for attempt in range(3):
        first = prompt("New password for " + name + " (min 12 characters, hidden): ")
        second = prompt("Same password again: ")
        if first != second:
            print("The two passwords are different. Try again.")
            continue
        problem = accounts.password_problem(first, name)
        if problem:
            print("Not accepted (" + problem + "). Try again.")
            continue
        accounts.create_superadmin(document, name, first)
        store.save_accounts(document)
        store.append_audit("account", name, accounts.ROLE_SUPERADMIN, "create_superadmin", name, "saved")
        print("Super admin '" + name + "' created. Store: " + str(store.folder) + " (key file: " + str(store.key_path) + ")")
        print("Keep the key file private and backed up with the store folder; without it the accounts cannot be read.")
        return 0
    print("refused: no password accepted after 3 tries. Nothing was created.")
    return 1


def status(args: argparse.Namespace) -> int:
    store = open_store(args)
    rows = accounts.public_view(store.load_accounts())
    print("store: " + str(store.folder))
    for row in rows:
        flags = [flag for flag in ("disabled", "locked", "must_change") if row[flag]]
        print("  " + row["username"] + "  " + row["role"] + ("  [" + ", ".join(flags) + "]" if flags else ""))
    print(str(len(rows)) + " account(s)")
    return 0


def verify_audit(args: argparse.Namespace) -> int:
    check = open_store(args).verify_audit()
    if check.ok:
        print("audit chain verified: " + str(check.entries) + " entries")
        return 0
    print("AUDIT CHAIN BROKEN at line " + str(check.broken_at) + " of " + str(check.entries) + " (" + check.reason + ")")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", help="store folder (default: the launcher's folder)")
    parser.add_argument("--key", help="key file (default: store.key inside the folder)")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-superadmin", help="create the one super admin (hidden password prompt)")
    init.add_argument("--username", required=True)
    commands.add_parser("status", help="list accounts (never passwords or hashes)")
    commands.add_parser("verify-audit", help="check the encrypted audit log chain")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-superadmin":
            return init_superadmin(args)
        if args.command == "status":
            return status(args)
        return verify_audit(args)
    except ImportError:
        print("refused: the 'cryptography' package is missing for this Python. Use the project's Python "
              "(.venv-renewal-playwright on the PC).")
        return 1
    except RuntimeError as error:  # secure_store.StoreError
        print("refused: store error " + str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
