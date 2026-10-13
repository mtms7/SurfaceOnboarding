#!/bin/sh
# REVIEW ARTIFACT - Step 4A (operator-run on the VM as root: sudo sh /tmp/r24-upload/_step4a_vm_list_leftovers.sh).
# READ-ONLY listing of release folders, /tmp and home leftovers, pilot state and restart-pending. Deletes, changes and
# restarts NOTHING; prints names, sizes, owners and dates only (no file contents, no secrets).
set -u
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding

echo "== 1. release folders in $OPT (size, owner, date) =="
du -sh "$OPT"/app* "$OPT"/incoming* "$OPT"/runtime "$OPT"/venv 2>/dev/null
ls -ld "$OPT"/app* "$OPT"/incoming* 2>/dev/null
echo "app points to: $(readlink "$OPT/app" || echo not-a-symlink)"

echo "== 2. leftovers in /tmp (names, sizes, dates) =="
ls -ld /tmp/step2*.sh /tmp/r2*-upload /tmp/r2*-vm-check.sh /tmp/r2*-suite.log /tmp/step3*.sh 2>/dev/null

echo "== 3. leftovers in the workato home (names, sizes, dates) =="
ls -ld /home/workato/r2*-upload 2>/dev/null
du -sh /home/workato/r2*-upload 2>/dev/null

echo "== 4. snapshots and state dir (names only) =="
ls -l /var/lib/surface-onboarding /var/lib/surface-onboarding/snapshots 2>/dev/null

echo "== 5. pilot state =="
systemctl is-active surface-onboarding-dashboard nginx || true
systemctl is-enabled surface-onboarding-dashboard nginx || true
ls -l /etc/surface-onboarding/approved-web-identity 2>&1 || true
ss -ltn | grep -E ':8000|:8443' || true

echo "== 6. restart pending / uptime =="
[ -f /var/run/reboot-required ] && { echo "reboot required:"; cat /var/run/reboot-required.pkgs 2>/dev/null; } || echo "no reboot required"
uptime
systemctl is-active workato-agent 2>/dev/null || true
echo "listing only; nothing was changed"
