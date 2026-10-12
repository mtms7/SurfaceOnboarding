#!/bin/sh
# REVIEW ARTIFACT - Step 3B (operator-run on the VM as root: sudo sh /tmp/r23-upload/_step3b_vm_test_r23.sh).
# Continues Step 3A after its unpack: runs the full suite and both guards on the already staged r23 with the managed
# Python. Read-only for everything except test temp files. Does NOT touch `app`, services, /etc/surface-onboarding.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
NEW=$OPT/app-r23-staged-20261012
RUNAS=surface-onboarding
PY=$OPT/runtime/python-3.12.14/bin/python3.12
[ -d "$NEW/integration/tests" ] || { echo "missing staged $NEW"; exit 1; }
[ -x "$PY" ] || { echo "managed python not found at $PY"; exit 1; }
"$PY" --version
cd "$NEW"
sudo -u "$RUNAS" "$PY" -m unittest discover -s integration/tests -t . 2>&1 | tail -6
sudo -u "$RUNAS" "$PY" tools/check_integration_artifacts.py
sudo -u "$RUNAS" "$PY" tools/check_integration_offline_boundary.py
echo "== state (expect app still -> r22, dashboard and nginx unchanged) =="
ls -ld "$OPT/app" "$NEW" "$OPT/incoming-r23"
systemctl is-active surface-onboarding-dashboard nginx || true
echo "staged and tested; nothing activated"
