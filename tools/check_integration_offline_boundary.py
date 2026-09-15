"""Reject external transport and process-execution capability in local scaffold code."""

from __future__ import annotations

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "integration" / "onboarding"
FORBIDDEN_IMPORT_ROOTS = frozenset({
    "aiohttp", "http", "httpx", "requests", "socket", "subprocess", "urllib", "websockets",
})
FORBIDDEN_CALLS = frozenset({"os.system", "os.popen", "subprocess.run", "subprocess.Popen"})


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def main() -> int:
    violations: list[str] = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for name in imported:
                if name.split(".", 1)[0] in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden import {name}")
            if isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                if name in FORBIDDEN_CALLS:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: forbidden call {name}")
    if violations:
        print("offline boundary violations:\n" + "\n".join(sorted(violations)))
        return 1
    print("integration offline boundary check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
