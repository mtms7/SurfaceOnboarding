#!/bin/sh
# REVIEW ARTIFACT - Step 18B (operator-run on the VM as root): sudo sh /tmp/step18b.sh [TEMPLATE_DIR]
#                                                              sudo sh /tmp/step18b.sh revert
# Turns on the hourly read-only Redash production-clone collection (docs/43 part B, docs/44 addendum 2026-10-16):
# checks the key file (step 18A), checks outbound reachability of https://redash.pentera.io (prints the HTTP status code only,
# sends NO key), creates the approval marker the service unit requires, installs the service and timer from the templates,
# `enable --now` the timer, shows status. Run ONLY after: the Redash owner's scheduled refresh on query 251, the SecOps
# allow-list, and your own approval (creating the marker IS that approval).
# revert: disable the timer, remove the marker and both units (the key file and the collected clone stay; 18A revert removes the key).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
DIR=/etc/surface-onboarding
KEY=$DIR/redash-key
MARKER=$DIR/approved-redash-collector
UNIT=surface-onboarding-redash-collect
SYSTEMD=/etc/systemd/system

if [ "${1:-}" = revert ]; then
  systemctl disable --now "$UNIT.timer" 2>/dev/null || true
  systemctl stop "$UNIT.service" 2>/dev/null || true
  rm -f "$MARKER" "$SYSTEMD/$UNIT.service" "$SYSTEMD/$UNIT.timer"
  systemctl daemon-reload
  echo "disabled and removed: timer, service, marker. kept: key file, collected clone."
  systemctl is-enabled "$UNIT.timer" 2>&1 || true
  exit 0
fi

T=${1:-/opt/surface-onboarding/app/integration/deployment/vm_pilot}

echo "== 1. checks =="
if [ ! -f "$T/$UNIT.service.template" ] || [ ! -f "$T/$UNIT.timer.template" ]; then echo "templates missing in $T: stop here"; exit 1; fi
[ -f "$KEY" ] || { echo "$KEY missing: run step 18A first"; exit 1; }
[ ! -L "$KEY" ] || { echo "key file is a link: stop here"; exit 1; }
KEYINFO=$(stat -c '%U:%G %a' "$KEY")
echo "key file: $KEYINFO (must be root:surface-redash-reader 640)"
[ "$KEYINFO" = "root:surface-redash-reader 640" ] || { echo "key file owner/mode wrong: stop here"; exit 1; }

echo "== 2. outbound reachability (status code only, no key sent) =="
CODE=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 https://redash.pentera.io/ 2>/dev/null || true)
echo "https://redash.pentera.io -> HTTP ${CODE:-none}"
case "${CODE:-}" in
  ''|000) echo "not reachable: the SecOps outbound allow-list is missing. Nothing installed."; exit 1 ;;
esac

echo "== 3. approval marker (this is the owner's go) =="
umask 0022
printf 'approved by operator %s at %s\n' "${SUDO_USER:-root}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"
chown root:root "$MARKER"
chmod 0644 "$MARKER"
ls -l "$MARKER"

echo "== 4. install units (from the templates, unchanged) =="
install -m 0644 -o root -g root "$T/$UNIT.service.template" "$SYSTEMD/$UNIT.service"
install -m 0644 -o root -g root "$T/$UNIT.timer.template" "$SYSTEMD/$UNIT.timer"
systemctl daemon-reload
systemd-analyze verify "$SYSTEMD/$UNIT.service" 2>&1 | head -n 10 || true

echo "== 5. enable the timer =="
systemctl enable --now "$UNIT.timer"
systemctl list-timers "$UNIT.timer" --no-pager || true
systemctl is-enabled "$UNIT.timer"
systemctl is-active "$UNIT.timer"
echo "first run is about 5 min after enable. check with step 18C. undo: sudo sh /tmp/step18b.sh revert"
