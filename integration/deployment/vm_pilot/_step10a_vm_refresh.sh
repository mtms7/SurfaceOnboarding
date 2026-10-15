#!/bin/sh
# REVIEW ARTIFACT - Step 10A (operator-run on the VM as root): sudo sh /tmp/r26-upload/_step10a_vm_refresh.sh dry-run | refresh
# dry-run: checks the refresher's configuration and key files only (no network, nothing read, nothing written).
# refresh: ONE read-only Salesforce refresh using the operator's in-memory CLI session (step 8C must have signed in first):
#          fixed queries only, no Salesforce write. Writes a new signed snapshot under /var/lib/surface-onboarding/snapshots and
#          refresh-status.json (time, result code, transport; no values). The dashboard reads the new snapshot on its next
#          request: no restart. Previous snapshots stay for rollback. Creates NO approval marker, NO timer, NO service.
# Prints only counts, status codes and CO numbers (no names, domains or values).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
MODE=${1:-}
case "$MODE" in dry-run|refresh) ;; *) echo "usage: sh _step10a_vm_refresh.sh dry-run|refresh"; exit 1;; esac
OPT=/opt/surface-onboarding
STATE=/var/lib/surface-onboarding
SESSION=/run/surface-onboarding-sf
PY=$OPT/runtime/python-3.12.14/bin/python3.12
KEY=/etc/surface-onboarding/snapshot.key
[ -x "$PY" ] && [ -f "$OPT/app/tools/vm_refresh_snapshot.py" ] || { echo "missing python or the r26 tool: stop here"; exit 1; }
[ "$(readlink "$OPT/app")" = "$OPT/app-r26-staged-20261015" ] || { echo "app is not r26: stop here"; exit 1; }
[ -f "$KEY" ] || { echo "snapshot key missing: stop here"; exit 1; }

run_tool() {
  cd "$OPT/app"
  sudo -u surface-onboarding env PYTHONDONTWRITEBYTECODE=1 SURFACE_ONBOARDING_STATE_DIR="$STATE" \
    SURFACE_SF_CLI=$OPT/sf-cli/bin/sf SURFACE_SF_TARGET_ORG=surface-onboarding SURFACE_SF_SESSION_DIR="$SESSION" \
    SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true SF_AUTOUPDATE_DISABLE=true SFDX_AUTOUPDATE_DISABLE=true \
    SF_SKIP_NEW_VERSION_CHECK=true \
    "$PY" tools/vm_refresh_snapshot.py --transport cli --session-dir "$SESSION" --key-file "$KEY" "$@"
}

echo "== 1. before: snapshots (names only) and the dashboard banner =="
ls "$STATE/snapshots" 2>/dev/null
curl -sk https://localhost:8443/ | grep -o -E 'Data as of[^<]{0,45}|Salesforce session needed' | sort | uniq -c || true

if [ "$MODE" = dry-run ]; then
  echo "== 2. dry run (no network, no write) =="
  run_tool --dry-run
  echo "dry run finished"
  exit 0
fi

echo "== 2. session present? (names and modes only) =="
ls -l "$SESSION/.sfdx" 2>/dev/null | sed 's/[A-Za-z0-9_.-]*@[A-Za-z0-9_.-]*/<user>/' || true
echo "== 3. ONE read-only refresh =="
rc=0; run_tool || rc=$?
echo "exit code: $rc"

echo "== 4. after: snapshots, status file (no values), banner, CO numbers on the queue page =="
ls "$STATE/snapshots"
cat "$STATE/refresh-status.json" 2>/dev/null; echo
curl -sk https://localhost:8443/ | grep -o -E 'Data as of[^<]{0,45}|Salesforce session needed' | sort | uniq -c || true
curl -sk https://localhost:8443/ | grep -o -E 'CO-[0-9]{4}' | sort -u | tr '\n' ' '; echo
echo "end the session when done: sudo sh /tmp/step8d.sh"
