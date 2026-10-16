"""Which environment a run targets. Development is the default; production stays locked.

Opens no connection and reads no files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from . import session_readiness
from .origin_policy import require_development_origin, require_production_origin


class ProductionLocked(Exception):
    """Production target refused; `reason` is the production_unlock_problem code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class RunTarget:
    environment_key: str
    origin: str
    profile_dir_name: str


def development_target() -> RunTarget:
    environment = session_readiness.ENVIRONMENTS[session_readiness.LEONARDO_DEVELOPMENT]
    return RunTarget(environment.key, require_development_origin(environment.origin),
                     environment.profile_dir_name)


def production_target(environ: Mapping[str, str], now: datetime) -> RunTarget:
    """Raise ProductionLocked unless every production unlock condition holds."""
    problem = session_readiness.production_unlock_problem(environ, now)
    if problem is not None:
        raise ProductionLocked(problem)
    environment = session_readiness.ENVIRONMENTS[session_readiness.BACKOFFICE_PRODUCTION]
    return RunTarget(environment.key, require_production_origin(environment.origin),
                     environment.profile_dir_name)
