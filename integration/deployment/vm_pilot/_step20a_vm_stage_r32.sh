#!/bin/sh
# REVIEW ARTIFACT - Step 20A (operator-run on the VM as root: sudo sh /tmp/r32-upload/_step20a_vm_stage_r32.sh).
# Stages release r32 only: verifies the SHA-256 sidecar, unpacks as surface-onboarding into a NEW staged folder,
# checks for CR bytes, runs the full suite and both guards with the managed Python. Does NOT touch the `app` symlink,
# the running dashboard/nginx, /etc/surface-onboarding, snapshots, or any external system. Activation is a later step.
# Expects /tmp/r32-upload/surface-onboarding-r32-5ffc0c3.tar.gz and its .sha256 (uploaded by the operator with scp).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
UP=/tmp/r32-upload
NAME=surface-onboarding-r32-5ffc0c3.tar.gz
INC=$OPT/incoming-r32
NEW=$OPT/app-r32-staged-20261016
RUNAS=surface-onboarding
[ -f "$UP/$NAME" ] && [ -f "$UP/$NAME.sha256" ] || { echo "missing upload in $UP"; exit 1; }
[ ! -e "$INC" ] || { echo "$INC already exists: stop here"; exit 1; }
[ ! -e "$NEW" ] || { echo "$NEW already exists: stop here"; exit 1; }

echo "== 1. verify SHA-256 (expect OK, expected 9803B12F...D6C6C) =="
EXPECTED=9803B12FCBC5070D9A347C3D4AB9BC403918EE97613592C00076F8A99E2D6C6C
ACTUAL=$(sha256sum "$UP/$NAME" | cut -d' ' -f1 | tr a-f A-F)
SIDECAR=$(cut -d' ' -f1 "$UP/$NAME.sha256")
echo "actual  $ACTUAL"; echo "sidecar $SIDECAR"
[ "$ACTUAL" = "$EXPECTED" ] && [ "$SIDECAR" = "$EXPECTED" ] || { echo "CHECKSUM MISMATCH: stop here"; exit 1; }
echo OK

echo "== 2. copy to incoming-r32 and list members for review =="
install -d -o "$RUNAS" -g "$RUNAS" -m 0750 "$INC"
install -o "$RUNAS" -g "$RUNAS" -m 0640 "$UP/$NAME" "$UP/$NAME.sha256" "$INC/"
tar -tzf "$INC/$NAME" | grep -E '(^/|(^|/)\.\.(/|$))' && { echo "unsafe member path: stop here"; exit 1; } || echo "no absolute or .. paths"
echo "members: $(tar -tzf "$INC/$NAME" | wc -l)"

echo "== 3. unpack as $RUNAS into $NEW =="
install -d -o "$RUNAS" -g "$RUNAS" -m 0750 "$NEW"
sudo -u "$RUNAS" tar -xzf "$INC/$NAME" -C "$NEW" --no-same-owner
ls "$NEW"

echo "== 4. CR byte check in text files (expect none) =="
if grep -rIl "$(printf '\r')" "$NEW" --include='*.py' --include='*.sh' --include='*.md' --include='*.template' --include='*.sql' --include='*.lock' --include='*.txt'; then
  echo "CR bytes found: stop here"; exit 1
fi
echo none

echo "== 5. full suite and both guards (managed Python, no network, no activation) =="
PY=$OPT/runtime/python-3.12.14/bin/python3.12
[ -x "$PY" ] || { echo "managed python not found at $PY"; exit 1; }
"$PY" --version
cd "$NEW"
sudo -u "$RUNAS" "$PY" -m unittest discover -s integration/tests -t . 2>&1 | tail -6
sudo -u "$RUNAS" "$PY" tools/check_integration_artifacts.py
sudo -u "$RUNAS" "$PY" tools/check_integration_offline_boundary.py

echo "== 6. state (expect app still -> r30, dashboard and nginx unchanged) =="
ls -ld "$OPT/app" "$NEW" "$INC"
systemctl is-active surface-onboarding-dashboard nginx || true
echo "staged only; nothing activated"
