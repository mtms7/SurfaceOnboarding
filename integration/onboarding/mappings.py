"""Versioned route registry; every executable mapping is disabled by default."""

from __future__ import annotations

from dataclasses import dataclass

from .models import EngineValue, ReasonCode


@dataclass(frozen=True, slots=True)
class MappingRegistration:
    engine_value: EngineValue
    version: str
    enabled: bool
    disabled_reason: ReasonCode | None

    def __post_init__(self) -> None:
        if not isinstance(self.engine_value, EngineValue):
            raise ValueError("invalid_mapping_engine_value")
        if not self.version or any(character.isspace() for character in self.version):
            raise ValueError("invalid_mapping_version")
        if self.enabled:
            if not self.version.startswith("approved-"):
                raise ValueError("enabled_mapping_requires_approved_version")
            if self.disabled_reason is not None:
                raise ValueError("enabled_mapping_cannot_have_disabled_reason")
        elif not isinstance(self.disabled_reason, ReasonCode):
            raise ValueError("disabled_mapping_requires_reason")


MAPPING_REGISTRY = {
    value: MappingRegistration(value, "unapproved-v1", False, ReasonCode.MAPPING_NOT_APPROVED)
    for value in EngineValue
}


def mapping_for(engine_value: EngineValue) -> MappingRegistration:
    return MAPPING_REGISTRY[engine_value]


def mapping_blocker(engine_value: object) -> ReasonCode | None:
    """Return a stable blocker unless this exact route has an enabled mapping."""
    if not isinstance(engine_value, EngineValue):
        return ReasonCode.CASE_NOT_MAPPED_YET
    registration = MAPPING_REGISTRY.get(engine_value)
    if registration is None:
        return ReasonCode.CASE_NOT_MAPPED_YET
    return None if registration.enabled else registration.disabled_reason
