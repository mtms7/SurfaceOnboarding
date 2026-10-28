#!/bin/sh
# REVIEW ARTIFACT - Step 28D (operator-run on the VM as root): sudo sh /tmp/_step28d_vm_remote_start_switch.sh on <owner-email> | off | status
# Owner decision 2026-10-27 (docs/62): switches the VM page button "Start onboarding on my PC" on or off. It adds or removes ONE systemd
# drop-in on the DASHBOARD unit (Environment=SURFACE_ONBOARDING_REMOTE_START=1 plus the ONE owner e-mail allowed to ask) and restarts the
# dashboard once (a few seconds). Without the owner e-mail the feature stays off. With the
# switch off, the button and its single owner-only POST do not exist and the VM refuses every POST, as before. The request only writes
# a short-lived file; the PC polls for it over the existing restricted key and still runs every create gate and waits for your SSO and MFA.
# It touches no snapshot, no marker, no key, no firewall rule, no other unit and no Salesforce data.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
UNIT=surface-onboarding-dashboard.service
DROPIN_DIR=/etc/systemd/system/$UNIT.d
DROPIN=$DROPIN_DIR/remote-start.conf
case "${1:-}" in
status)
  echo "remote start drop-in: $([ -f "$DROPIN" ] && echo ON || echo off)"
  systemctl is-active "$UNIT"
  exit 0;;
on)
  OWNER=${2:-}
  printf '%s' "$OWNER" | grep -Eq '^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,80}$' || { echo "usage: sh _step28d_vm_remote_start_switch.sh on <owner-email>"; exit 1; }
  [ -f "/etc/systemd/system/$UNIT" ] || [ -f "/lib/systemd/system/$UNIT" ] || { echo "dashboard unit not found: stop here"; exit 1; }
  install -d -m 0755 -o root -g root "$DROPIN_DIR"
  printf '[Service]\nEnvironment=SURFACE_ONBOARDING_REMOTE_START=1\nEnvironment=SURFACE_ONBOARDING_REMOTE_START_OWNER=%s\n' "$OWNER" > "$DROPIN"
  chmod 0644 "$DROPIN"
  systemctl daemon-reload; systemctl restart "$UNIT";;
off)
  rm -f "$DROPIN"; rmdir "$DROPIN_DIR" 2>/dev/null || true
  systemctl daemon-reload; systemctl restart "$UNIT";;
*) echo "usage: sh _step28d_vm_remote_start_switch.sh on <owner-email> | off | status"; exit 1;;
esac
sleep 3
systemctl is-active "$UNIT" || { journalctl -u "$UNIT" -n 20 --no-pager | cut -c1-160; echo "dashboard did not start: run this script with off"; exit 1; }
curl -sk -o /dev/null -w "GET / -> %{http_code} (expect 200)\n" https://localhost:8443/
echo "remote start drop-in: $([ -f "$DROPIN" ] && echo ON || echo off)"
