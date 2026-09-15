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

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${SURFACE_ONBOARDING_PYTHON:-python3}"
exec "$python_bin" "$project_root/tools/serve_attended_open_onboardings_dashboard.py"
