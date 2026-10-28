#!/bin/sh
# REVIEW ARTIFACT - Step 31A (operator-run on the VM as root: sudo sh /tmp/r41-upload/_step31a_vm_stage_r41.sh).
# Stages release r41 only: verifies the SHA-256 sidecar, unpacks as surface-onboarding into a NEW staged folder,
# checks for CR bytes, runs the full suite and both guards with the managed Python. Does NOT touch the `app` symlink,
# the running dashboard/nginx, /etc/surface-onboarding, snapshots, or any external system. Activation is a later step.
# Expects /tmp/r41-upload/surface-onboarding-r41-5e6984c.tar.gz and its .sha256 (uploaded by the operator with scp).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
UP=/tmp/r41-upload
NAME=surface-onboarding-r41-5e6984c.tar.gz
INC=$OPT/incoming-r41
NEW=$OPT/app-r41-staged-20261027
RUNAS=surface-onboarding
[ -f "$UP/$NAME" ] && [ -f "$UP/$NAME.sha256" ] || { echo "missing upload in $UP"; exit 1; }
[ ! -e "$INC" ] || { echo "$INC already exists: stop here"; exit 1; }
[ ! -e "$NEW" ] || { echo "$NEW already exists: stop here"; exit 1; }

echo "== 1. verify SHA-256 (expect OK, expected 0CE99BC9...B5018) =="
EXPECTED=0CE99BC9888355B89A415E4201FD12D9DDAAFFB8DF0CEB959C96D9A8519B5018
ACTUAL=$(sha256sum "$UP/$NAME" | cut -d' ' -f1 | tr a-f A-F)
SIDECAR=$(cut -d' ' -f1 "$UP/$NAME.sha256")
echo "actual  $ACTUAL"; echo "sidecar $SIDECAR"
[ "$ACTUAL" = "$EXPECTED" ] && [ "$SIDECAR" = "$EXPECTED" ] || { echo "CHECKSUM MISMATCH: stop here"; exit 1; }
echo OK

echo "== 2. copy to incoming-r41 and list members for review =="
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

echo "== 6. state (expect app still -> r39, dashboard and nginx unchanged) =="
ls -ld "$OPT/app" "$NEW" "$INC"
systemctl is-active surface-onboarding-dashboard nginx || true
echo "staged only; nothing activated"
