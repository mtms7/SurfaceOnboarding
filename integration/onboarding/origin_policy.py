"""Exact allowlist for a future Leonardo adapter; this module opens no connection."""

from __future__ import annotations

LEONARDO_DEVELOPMENT_ORIGIN = "https://leonardo.dev.app.pentera.io"


def require_development_origin(origin: str) -> str:
    """Return the exact approved development origin or reject every variation."""
    if not isinstance(origin, str):
        raise ValueError("invalid_leonardo_origin")
    if origin != LEONARDO_DEVELOPMENT_ORIGIN:
        raise ValueError("leonardo_origin_not_approved")
    return LEONARDO_DEVELOPMENT_ORIGIN
