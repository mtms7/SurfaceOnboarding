"""Small CI-friendly guard against accidental sensitive integration artifacts."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "integration"
FORBIDDEN_SUFFIXES = {".har", ".pem", ".key", ".p12", ".pfx", ".sqlite", ".db"}
FORBIDDEN_NAME_PARTS = {"password", "credential", "cookie", "token", "secret", "screenshot"}
SOURCE_SUFFIXES = {".md", ".py", ".toml", ".yaml", ".yml"}


def main() -> int:
    violations = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        name_is_sensitive = any(part in path.name.lower() for part in FORBIDDEN_NAME_PARTS)
        if path.suffix.lower() in FORBIDDEN_SUFFIXES or (name_is_sensitive and path.suffix.lower() not in SOURCE_SUFFIXES):
            violations.append(path.relative_to(ROOT).as_posix())
    if violations:
        print("forbidden integration artifacts: " + ", ".join(sorted(violations)))
        return 1
    print("integration artifact check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
