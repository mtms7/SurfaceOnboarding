#!/bin/sh
# REVIEW ARTIFACT - Step 28C (operator-run on the VM as root): sudo sh /tmp/_step28c_vm_hide_sf_session_from_dashboard.sh install | status | revert
# Security review 2026-10-27: the internet-facing dashboard runs as the same user (surface-onboarding) that owns the in-memory Salesforce
# CLI session in /run/surface-onboarding-sf, so any code execution inside the dashboard could read the CLI token. This adds ONE systemd
# drop-in to the DASHBOARD unit only: InaccessiblePaths=-/run/surface-onboarding-sf (the leading "-" ignores a missing folder), so the
# dashboard's own mount namespace no longer shows the folder at all. The refresh unit, the login program, the approval marker, the
# snapshots, the web proxy and the clone push are untouched. It restarts the dashboard once (a few seconds) and checks GET / answers 200.
# `revert` removes the drop-in and restarts the dashboard again. A separate user for the refresher is a later, larger step.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
UNIT=surface-onboarding-dashboard.service
DROPIN_DIR=/etc/systemd/system/$UNIT.d
DROPIN=$DROPIN_DIR/hide-sf-session.conf
case "${1:-}" in
status)
  echo "drop-in: $([ -f "$DROPIN" ] && echo present || echo absent)"
  systemctl is-active "$UNIT"
  systemctl show "$UNIT" -p InaccessiblePaths --value 2>/dev/null | sed 's/^/InaccessiblePaths: /'
  exit 0;;
revert)
  rm -f "$DROPIN"; rmdir "$DROPIN_DIR" 2>/dev/null || true
  systemctl daemon-reload; systemctl restart "$UNIT";;
install)
  [ -f "/etc/systemd/system/$UNIT" ] || [ -f "/lib/systemd/system/$UNIT" ] || { echo "dashboard unit not found: stop here"; exit 1; }
  install -d -m 0755 -o root -g root "$DROPIN_DIR"
  printf '[Service]\nInaccessiblePaths=-/run/surface-onboarding-sf\n' > "$DROPIN"
  chmod 0644 "$DROPIN"
  systemctl daemon-reload; systemctl restart "$UNIT";;
*) echo "usage: sh _step28c_vm_hide_sf_session_from_dashboard.sh install | status | revert"; exit 1;;
esac
sleep 3
systemctl is-active "$UNIT" || { journalctl -u "$UNIT" -n 20 --no-pager | cut -c1-160; echo "dashboard did not start: run this script with revert"; exit 1; }
curl -sk -o /dev/null -w "GET / -> %{http_code} (expect 200)\n" https://localhost:8443/
echo "drop-in: $([ -f "$DROPIN" ] && echo present || echo absent)"
