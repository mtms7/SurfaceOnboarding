"""Optional FastAPI entry point using synthetic status only until SSO is approved."""

from __future__ import annotations

from .adapters import MockSalesforceAdapter, SyncScope
from .config import AppConfig


def create_app(config: AppConfig):
    """Create a no-auth, non-deployable local development app.

    A production listener must not be enabled until the documented individual
    identity, CSRF, TLS, and reverse-proxy gates are approved.
    """
    if not config.web_enabled:
        raise RuntimeError("web_listener_requires_identity_approval")
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:  # pragma: no cover - dependency installation gate
        raise RuntimeError("install integration FastAPI dependencies before running the web scaffold") from exc

    app = FastAPI(title="Surface onboarding local scaffold", docs_url=None, redoc_url=None)
    adapter = MockSalesforceAdapter()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "local_scaffold_only"}

    @app.post("/synthetic-sync/{scope_kind}")
    def synthetic_sync(scope_kind: str, identifier: str | None = None) -> dict[str, str]:
        try:
            result = adapter.request_sync(SyncScope(scope_kind, identifier), requested_by="local-test")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"job_id": result.job_id, "outcome": "accepted"}

    return app
