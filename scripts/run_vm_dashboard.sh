#!/usr/bin/env bash
# Guarded Ubuntu 22 dashboard launcher. It is not an installation or
# activation mechanism; systemd/proxy/identity approval remains required.
set -euo pipefail

if [[ "${SURFACE_ONBOARDING_RUNTIME:-}" != "vm" ]]; then
  echo "Refusing to start: set SURFACE_ONBOARDING_RUNTIME=vm explicitly." >&2
  exit 2
fi

if [[ "${SURFACE_ONBOARDING_WEB_IDENTITY_APPROVED:-}" != "1" ]]; then
  echo "Refusing to start: approved web identity marker is required." >&2
  exit 2
fi

if [[ "${SURFACE_ONBOARDING_HOST:-127.0.0.1}" != "127.0.0.1" ]]; then
  echo "Refusing to start: dashboard must bind only to 127.0.0.1." >&2
  exit 2
fi

# Team-access mode (docs/40 step 0): identity comes from the loopback proxy, so the allow-list and the one public
# https origin must be configured; otherwise every request would be refused anyway. Fail early and clearly.
if [[ -z "${SURFACE_ONBOARDING_ALLOWED_USERS:-}" && -z "${SURFACE_ONBOARDING_ALLOWED_USERS_FILE:-}" ]]; then
  echo "Refusing to start: SURFACE_ONBOARDING_ALLOWED_USERS (or _FILE) must list the allowed e-mails." >&2
  exit 2
fi

if [[ ! "${SURFACE_ONBOARDING_PUBLIC_ORIGIN:-}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?$ ]]; then
  echo "Refusing to start: SURFACE_ONBOARDING_PUBLIC_ORIGIN must be an https origin such as https://host:8443." >&2
  exit 2
fi

# Local state lives under one folder (mode 0750, owner surface-onboarding).
export SURFACE_ONBOARDING_STATE_DIR="${SURFACE_ONBOARDING_STATE_DIR:-/var/lib/surface-onboarding}"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Ubuntu 22.04's system ``python3`` is 3.10, but this project requires
# Python 3.12.  Prefer an explicitly approved override for a managed runtime;
# otherwise use the VM runtime installed alongside the onboarding application.
python_bin="${SURFACE_ONBOARDING_PYTHON:-/opt/surface-onboarding/runtime/python-3.12.14/bin/python3.12}"

if [[ ! -x "$python_bin" ]]; then
  echo "Refusing to start: Python 3.12 runtime is unavailable at $python_bin." >&2
  exit 2
fi

if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  echo "Refusing to start: $python_bin must be Python 3.12 or newer." >&2
  exit 2
fi

exec "$python_bin" "$project_root/tools/serve_attended_open_onboardings_dashboard.py"
