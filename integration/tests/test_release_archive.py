from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest

from tools.check_release_archive import REQUIRED_MEMBERS, check_release_archive


class ReleaseArchiveTests(unittest.TestCase):
    def _write_archive(self, directory: Path, members: dict[str, bytes]) -> tuple[Path, Path]:
        archive = directory / "release.tar.gz"
        source = directory / "source"
        source.mkdir()
        with tarfile.open(archive, "w:gz") as release:
            for name, contents in members.items():
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
                release.add(path, arcname=name, recursive=False)
        sidecar = directory / "release.tar.gz.sha256"
        sidecar.write_text(
            f"{sha256(archive.read_bytes()).hexdigest().upper()}  {archive.name}\n",
            encoding="ascii",
        )
        return archive, sidecar

    def test_valid_release_layout_passes(self):
        with TemporaryDirectory() as temp:
            archive, sidecar = self._write_archive(
                Path(temp), {name: b"safe" for name in REQUIRED_MEMBERS},
            )
            self.assertEqual(check_release_archive(archive, sidecar), 0)

    def test_unpacked_size_cap_is_enforced_and_allows_a_release_of_the_current_size(self):
        from tools.check_release_archive import MAX_UNPACKED_BYTES
        self.assertGreaterEqual(MAX_UNPACKED_BYTES, 8 * 1024 * 1024)
        with TemporaryDirectory() as temp:
            members = {name: b"safe" for name in REQUIRED_MEMBERS}
            chunk = b"0" * (900 * 1024)  # compresses to almost nothing; each member stays under the per-member cap
            for index in range(MAX_UNPACKED_BYTES // len(chunk) + 2):
                members[f"integration/filler/part{index}.txt"] = chunk
            archive, sidecar = self._write_archive(Path(temp), members)
            self.assertEqual(check_release_archive(archive, sidecar), 1)

    def test_checksum_and_required_members_fail_closed(self):
        with TemporaryDirectory() as temp:
            archive, sidecar = self._write_archive(Path(temp), {"integration/onboarding/__init__.py": b"safe"})
            self.assertEqual(check_release_archive(archive, sidecar), 1)
            sidecar.write_text("0" * 64 + "  release.tar.gz\n", encoding="ascii")
            self.assertEqual(check_release_archive(archive, sidecar), 1)

    def test_symlink_member_is_rejected(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp)
            archive = directory / "release.tar.gz"
            with tarfile.open(archive, "w:gz") as release:
                for name in REQUIRED_MEMBERS:
                    info = tarfile.TarInfo(name)
                    info.size = 4
                    release.addfile(info, __import__("io").BytesIO(b"safe"))
                link = tarfile.TarInfo("integration/onboarding/link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                release.addfile(link)
            sidecar = directory / "release.tar.gz.sha256"
            sidecar.write_text(
                f"{sha256(archive.read_bytes()).hexdigest().upper()}  {archive.name}\n",
                encoding="ascii",
            )
            self.assertEqual(check_release_archive(archive, sidecar), 1)
