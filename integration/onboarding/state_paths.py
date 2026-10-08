"""One place that decides where local onboarding state lives (docs/40 section 5).

Desktop (Windows attended pilot): unchanged, the historic locations are used.
``SURFACE_ONBOARDING_STATE_DIR`` (an absolute path) moves every state file there; in VM mode with
no override the folder is ``/var/lib/surface-onboarding``. Resolution happens when a module is
imported (the runner and dashboard keep their module constants, which tests patch), except for the
inventory root, which is resolved per call. Nothing is created here.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

STATE_DIR_ENV = "SURFACE_ONBOARDING_STATE_DIR"
VM_DEFAULT_STATE_DIR = "/var/lib/surface-onboarding"
INVENTORY_SUBDIR = "leonardo-inventory"


def vm_mode() -> bool:
    return os.environ.get("SURFACE_ONBOARDING_RUNTIME", "desktop").casefold() == "vm"


def state_dir() -> Path | None:
    """The configured state folder, or None when today's paths apply (desktop without an override).

    A relative or empty override is rejected (fail closed) rather than resolved against the cwd.
    """
    configured = os.environ.get(STATE_DIR_ENV, "").strip()
    if configured:
        # Accept POSIX or Windows absolute spellings; relative paths are never used.
        if not (PurePosixPath(configured).is_absolute() or Path(configured).is_absolute()):
            raise RuntimeError("invalid_state_dir_configuration")
        return Path(configured)
    if vm_mode():
        return Path(VM_DEFAULT_STATE_DIR)
    return None


def state_file(default: Path) -> Path:
    """``default`` unchanged on the desktop; ``<state dir>/<file name>`` when a state folder applies."""
    folder = state_dir()
    return default if folder is None else folder / default.name


def inventory_root(default: Path) -> Path:
    """The Leonardo / Redash inventory snapshot root: ``<state dir>/leonardo-inventory`` or ``default``."""
    folder = state_dir()
    return default if folder is None else folder / INVENTORY_SUBDIR
