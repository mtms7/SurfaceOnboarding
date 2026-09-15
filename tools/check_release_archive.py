"""Verify a staged-release archive without extracting it.

This deliberately offline checker validates the release checksum and rejects
unsafe tar members before an operator extracts the archive with elevated host
permissions. It does not install, connect to, or execute archive contents.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile


MAX_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_UNPACKED_BYTES = 5 * 1024 * 1024
REQUIRED_MEMBERS = frozenset({
    "integration/onboarding/__init__.py",
    "integration/onboarding/dashboard_view.py",
    "integration/onboarding/salesforce_detail.py",
    "integration/onboarding/salesforce_detail_provider.py",
    "integration/onboarding/web_activation.py",
    "integration/tests/test_scaffold.py",
    "integration/tests/test_dashboard_view.py",
    "integration/tests/test_salesforce_detail.py",
    "integration/tests/test_salesforce_detail_provider.py",
    "integration/tests/test_web_activation.py",
    "integration/deployment/SECURITY_BASELINE.md",
    "integration/deployment/SALESFORCE_READ_APPROVAL_PACKET.md",
    "integration/deployment/REVERSE_PROXY_APPROVAL_PACKET.md",
    "integration/deployment/THREAT_MODEL.md",
    "integration/migrations/001_initial_metadata.sql",
    "integration/requirements.runtime.lock",
    "tools/check_integration_artifacts.py",
    "tools/check_integration_offline_boundary.py",
    "tools/check_release_archive.py",
    "tools/serve_loopback_preview.py",
})
CHECKSUM_PATTERN = re.compile(r"^[A-F0-9]{64}  [A-Za-z0-9][A-Za-z0-9._-]*\.tar\.gz$")


def _failure(message: str) -> int:
    print(f"release archive check failed: {message}")
    return 1


def _expected_checksum(sidecar: Path, archive: Path) -> str | None:
    try:
        lines = sidecar.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError):
        return None
    if len(lines) != 1 or not CHECKSUM_PATTERN.fullmatch(lines[0]):
        return None
    digest, filename = lines[0].split("  ", 1)
    if filename != archive.name:
        return None
    return digest


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def check_release_archive(archive: Path, sidecar: Path) -> int:
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE_BYTES:
        return _failure("archive_missing_or_oversize")
    expected = _expected_checksum(sidecar, archive)
    if expected is None:
        return _failure("checksum_sidecar_invalid")
    actual = sha256(archive.read_bytes()).hexdigest().upper()
    if actual != expected:
        return _failure("checksum_mismatch")
    try:
        with tarfile.open(archive, "r:gz") as release:
            members = release.getmembers()
    except (OSError, tarfile.TarError):
        return _failure("archive_unreadable")

    names: set[str] = set()
    unpacked_bytes = 0
    for member in members:
        if not _safe_name(member.name) or member.name in names:
            return _failure("unsafe_or_duplicate_member")
        names.add(member.name)
        if member.isdir():
            continue
        if not member.isfile() or member.size < 0 or member.size > MAX_MEMBER_BYTES:
            return _failure("unsafe_or_oversize_member")
        unpacked_bytes += member.size
        if unpacked_bytes > MAX_UNPACKED_BYTES:
            return _failure("unpacked_size_limit_exceeded")
    if not REQUIRED_MEMBERS <= names:
        return _failure("required_member_missing")
    print("release archive check passed")
    return 0


def main(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return _failure("usage: check_release_archive.py ARCHIVE CHECKSUM_SIDECAR")
    return check_release_archive(Path(arguments[0]), Path(arguments[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
