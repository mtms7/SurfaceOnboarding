#!/bin/sh
# REVIEW ARTIFACT - Step 8D (operator-run on the VM as root: sudo sh /tmp/step8d.sh). Spike for docs/43 section 8.
# Ends the in-memory Salesforce session: asks Salesforce to revoke the CLI's token (`sf org logout`), then removes everything in
# /run/surface-onboarding-sf. Safe to run when no session exists. Touches nothing else (no service, no snapshot, no marker).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
SF=/opt/surface-onboarding/sf-cli/bin/sf
SESSION=/run/surface-onboarding-sf
RUNAS=surface-onboarding
ALIAS=surface-onboarding
[ -d "$SESSION" ] || { echo "no session folder: nothing to do"; exit 0; }
case "$SESSION" in /run/*) ;; *) echo "unexpected session path"; exit 1;; esac

echo "== 1. revoke the CLI session at Salesforce (best effort) =="
if [ -x "$SF" ]; then
  sudo -u "$RUNAS" env HOME="$SESSION" SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true \
    "$SF" org logout --target-org "$ALIAS" --no-prompt 2>&1 | sed 's/[A-Za-z0-9_.-]*@[A-Za-z0-9_.-]*/<user>/g' | cut -c1-160 || true
fi

echo "== 2. remove everything in memory =="
find "$SESSION" -mindepth 1 -delete
ls -la "$SESSION" | sed -n '1,4p'
echo "session ended; the next refresh will say 'Salesforce session needed' until someone signs in again"
