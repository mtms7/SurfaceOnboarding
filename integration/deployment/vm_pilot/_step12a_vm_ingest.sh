#!/bin/sh
# REVIEW ARTIFACT - Step 12A (operator-run on the VM as root): sudo sh /tmp/r27-upload/_step12a_vm_ingest.sh <snapshot-zip-name>
# Verifies (HMAC, caps, names) and ingests ONE signed snapshot zip published from the desktop (docs/41, owner decision 2026-10-14:
# the production clone travels in the signed snapshot). The zip must be in /tmp/r27-upload/. Any failure leaves the current snapshot
# intact. Writes a new snapshot folder under /var/lib/surface-onboarding/snapshots and switches `current`; the dashboard reads it on
# its next request (no restart). Touches no service, marker, timer, network or Salesforce. Prints names, counts and status only.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
ZIPNAME=${1:-}
printf '%s' "$ZIPNAME" | grep -Eq '^surface-vm-snapshot-[0-9]{8}T[0-9]{6}Z\.zip$' || { echo "usage: sh _step12a_vm_ingest.sh surface-vm-snapshot-YYYYMMDDTHHMMSSZ.zip"; exit 1; }
OPT=/opt/surface-onboarding
STATE=/var/lib/surface-onboarding
ZIP=/tmp/r27-upload/$ZIPNAME
PY=$OPT/runtime/python-3.12.14/bin/python3.12
KEY=/etc/surface-onboarding/snapshot.key
[ -f "$ZIP" ] && [ -f "$KEY" ] && [ -x "$PY" ] || { echo "missing zip, key or python: stop here"; exit 1; }
chmod 0644 "$ZIP"
echo "== 1. before: snapshots (names) =="; ls "$STATE/snapshots"
echo "== 2. ingest =="
cd "$OPT/app"
sudo -u surface-onboarding env PYTHONDONTWRITEBYTECODE=1 SURFACE_ONBOARDING_STATE_DIR="$STATE" \
  "$PY" tools/ingest_vm_snapshot.py --key-file "$KEY" --zip "$ZIP"
echo "== 3. after: snapshots, inventory files in current (names and sizes only) =="
ls "$STATE/snapshots"
ls -l "$STATE/snapshots/current/" | awk '{print $5, $9}' | sed -n '1,30p'
echo "== 4. dashboard banner and the CO-0780 production check (match counts only) =="
curl -sk https://localhost:8443/ | grep -o -E 'Data as of[^<]{0,45}' | sort | uniq -c || true
for q in "production_clone_unavailable" "clone unavailable" "Not checked"; do
  printf '%s: ' "$q"; curl -sk "https://localhost:8443/co/CO-0780?env=prod" | grep -c -i "$q" || true
done
echo "done"
