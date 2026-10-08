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

from . import vm_snapshot

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


SNAPSHOT_CURRENT = vm_snapshot.CURRENT_LINK


def snapshot_current_dir(resolve: bool = False) -> Path | None:
    """VM data source switch (docs/41): in VM mode, ``<state dir>/snapshots/current``, the published desktop data.

    The ``current`` pointer is a symlink on POSIX that the ingest tool switches atomically, so a path built through
    it always reads the newest verified snapshot. Module constants built at import time must use that stable path
    (``resolve=False``); per-call readers may resolve the pointer (``resolve=True``, also where no symlink exists).
    Desktop mode: None (nothing changes).
    """
    if not vm_mode():
        return None
    folder = state_dir()
    if folder is None:
        return None
    root = vm_snapshot.snapshots_root(folder)
    return (vm_snapshot.current_dir(root) if resolve else None) or root / SNAPSHOT_CURRENT


def state_file(default: Path) -> Path:
    """``default`` unchanged on the desktop; ``<state dir>/<file name>`` when a state folder applies.

    VM mode reads the published snapshot instead: ``<state dir>/snapshots/current/<file name>``.
    """
    current = snapshot_current_dir()
    if current is not None:
        return current / default.name
    folder = state_dir()
    return default if folder is None else folder / default.name


def inventory_root(default: Path) -> Path:
    """The Leonardo / Redash inventory snapshot root: ``<state dir>/leonardo-inventory`` or ``default``.

    VM mode: the Dev inventory unpacked from the published snapshot (``.../snapshots/current/leonardo-inventory``).
    """
    current = snapshot_current_dir(resolve=True)
    if current is not None:
        return current / INVENTORY_SUBDIR
    folder = state_dir()
    return default if folder is None else folder / INVENTORY_SUBDIR
