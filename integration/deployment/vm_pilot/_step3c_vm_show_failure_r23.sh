#!/bin/sh
# REVIEW ARTIFACT - Step 3C (operator-run on the VM as root). Re-runs the r23 suite and prints ONLY the failing
# test names and their tracebacks (no passing output). Read-only; touches nothing but test temp files.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
PY=$OPT/runtime/python-3.12.14/bin/python3.12
cd "$OPT/app-r23-staged-20261012"
sudo -u surface-onboarding "$PY" -m unittest discover -s integration/tests -t . > /tmp/r23-suite.log 2>&1 || true
grep -n "^FAIL:\|^ERROR:" /tmp/r23-suite.log
awk '/^(FAIL|ERROR): /{p=1} p' /tmp/r23-suite.log | grep -v "^release archive check" | head -60
tail -3 /tmp/r23-suite.log
