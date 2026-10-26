#!/bin/sh
# REVIEW ARTIFACT - Step 26A (operator-run on the VM as root: sudo sh /tmp/r37-upload/_step26a_vm_stage_r37.sh).
# Stages release r37 only: verifies the SHA-256 sidecar, unpacks as surface-onboarding into a NEW staged folder,
# checks for CR bytes, runs the full suite and both guards with the managed Python. Does NOT touch the `app` symlink,
# the running dashboard/nginx, /etc/surface-onboarding, snapshots, or any external system. Activation is a later step.
# Expects /tmp/r37-upload/surface-onboarding-r37-44d25a2.tar.gz and its .sha256 (uploaded by the operator with scp).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
UP=/tmp/r37-upload
NAME=surface-onboarding-r37-44d25a2.tar.gz
INC=$OPT/incoming-r37
NEW=$OPT/app-r37-staged-20261026
RUNAS=surface-onboarding
[ -f "$UP/$NAME" ] && [ -f "$UP/$NAME.sha256" ] || { echo "missing upload in $UP"; exit 1; }
[ ! -e "$INC" ] || { echo "$INC already exists: stop here"; exit 1; }
[ ! -e "$NEW" ] || { echo "$NEW already exists: stop here"; exit 1; }

echo "== 1. verify SHA-256 (expect OK, expected 169E8B45...55456) =="
EXPECTED=169E8B45B8D05D668B48F18BE46F3D1387E39741947390163E70A03DBE5D5456
ACTUAL=$(sha256sum "$UP/$NAME" | cut -d' ' -f1 | tr a-f A-F)
SIDECAR=$(cut -d' ' -f1 "$UP/$NAME.sha256")
echo "actual  $ACTUAL"; echo "sidecar $SIDECAR"
[ "$ACTUAL" = "$EXPECTED" ] && [ "$SIDECAR" = "$EXPECTED" ] || { echo "CHECKSUM MISMATCH: stop here"; exit 1; }
echo OK

echo "== 2. copy to incoming-r37 and list members for review =="
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

echo "== 6. state (expect app still -> r36, dashboard and nginx unchanged) =="
ls -ld "$OPT/app" "$NEW" "$INC"
systemctl is-active surface-onboarding-dashboard nginx || true
echo "staged only; nothing activated"
