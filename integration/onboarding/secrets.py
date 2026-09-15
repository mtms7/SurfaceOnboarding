"""Secret-provider boundary without a secret retrieval capability.

An approved future provider may report audited configuration health, but secret
resolution is intentionally absent until an owner-approved implementation
exists. This keeps secret values out of the scaffold's contracts and logs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class SecretProviderStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    READY = "ready"


class SecretProvider(Protocol):
    def health(self) -> SecretProviderStatus: ...


class DisabledSecretProvider:
    def health(self) -> SecretProviderStatus:
        return SecretProviderStatus.NOT_CONFIGURED
