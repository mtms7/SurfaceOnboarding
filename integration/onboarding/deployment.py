"""Non-deploying host profile contracts for the future Ubuntu service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HostPlatform(StrEnum):
    UBUNTU_2204 = "ubuntu_22_04"


@dataclass(frozen=True, slots=True)
class HostDeploymentProfile:
    """Approved-host intent only; this class has no SSH, process, or I/O capability."""

    hostname: str
    platform: HostPlatform
    web_bind_host: str = "127.0.0.1"
    web_bind_port: int = 8000

    def __post_init__(self) -> None:
        if self.hostname != "workato-opa-01":
            raise ValueError("unapproved_deployment_host")
        if self.platform is not HostPlatform.UBUNTU_2204:
            raise ValueError("unsupported_deployment_platform")
        if self.web_bind_host != "127.0.0.1":
            raise ValueError("web_listener_must_bind_loopback")
        if not isinstance(self.web_bind_port, int) or isinstance(self.web_bind_port, bool):
            raise ValueError("invalid_web_bind_port")
        if not 1024 <= self.web_bind_port <= 65535:
            raise ValueError("invalid_web_bind_port")

