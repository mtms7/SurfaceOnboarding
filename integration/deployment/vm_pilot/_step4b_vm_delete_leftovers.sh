#!/bin/sh
# REVIEW ARTIFACT - Step 4B (operator-run on the VM as root: sudo sh /tmp/r24-upload/_step4b_vm_delete_leftovers.sh).
# Deletes ONLY the exact, owner-approved (2026-10-13) leftovers listed below. No wildcards. Refuses to run if `app`
# or a running process uses a target. Keeps r24 (active), r22 + incoming-r22 (rollback), app-prev-20260910, runtime,
# venv, snapshots, /tmp/step2c.sh (start/stop) and /tmp/r24-upload (activate/rollback). Does not touch services,
# /etc/surface-onboarding, the firewall or any external system.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
DIRS="
$OPT/app-r23-staged-20261012
$OPT/incoming-r23
$OPT/app-r19-staged-20261001
$OPT/app-r20-staged-20261001
$OPT/app-r21-staged-20261008
$OPT/incoming-r19
$OPT/incoming-r20
$OPT/incoming-r21
/tmp/r22-upload
/tmp/r23-upload
/home/workato/r21-upload
"
FILES="
/tmp/r21-vm-check.sh
/tmp/r23-suite.log
/tmp/step2a.sh
/tmp/step2b.sh
"
APP=$(readlink "$OPT/app" || true)
echo "== 1. safety checks (app -> $APP) =="
for p in $DIRS $FILES; do
  [ "$p" != "$APP" ] || { echo "REFUSING: app points to $p"; exit 1; }
  [ ! -L "$p" ] || { echo "REFUSING: $p is a symlink"; exit 1; }
  if pgrep -af -- "$p" | grep -v "pgrep\|$0"; then echo "REFUSING: a process uses $p"; exit 1; fi
done
echo "ok: no target is the active app, a symlink, or in use"

echo "== 2. targets that exist now =="
for p in $DIRS; do [ -d "$p" ] && echo "dir  $p"; done
for p in $FILES; do [ -f "$p" ] && echo "file $p"; done

echo "== 3. delete (exact names only) =="
for p in $DIRS; do [ -d "$p" ] && { rm -rf -- "$p"; echo "removed dir  $p"; }; done
for p in $FILES; do [ -f "$p" ] && { rm -f -- "$p"; echo "removed file $p"; }; done

echo "== 4. what remains =="
ls -d "$OPT"/app* "$OPT"/incoming* "$OPT"/runtime "$OPT"/venv
ls -d /tmp/step2c.sh /tmp/r24-upload
echo "app -> $(readlink "$OPT/app")"
systemctl is-active surface-onboarding-dashboard nginx || true
curl -sk -o /dev/null -w "GET / -> %{http_code} (expect 200)\n" https://localhost:8443/
