#!/bin/sh
# REVIEW ARTIFACT - Step 5A (operator-run on the VM as root: sudo sh /tmp/r24-upload/_step5a_vm_enable_at_boot.sh [revert]).
# Owner-approved 2026-10-13: the loopback-only pilot (dashboard + nginx) starts at boot, so a reboot no longer leaves it down.
# Also copies the pilot control scripts to /opt/surface-onboarding/ops/ (survives reboots; /tmp does not).
# Changes ONLY: `systemctl enable` for the two units and the ops folder. Starts/restarts/stops NOTHING now, does not touch
# the marker, the unit files, /etc/surface-onboarding, nginx config, the firewall, or any other service.
# The dashboard unit still has ConditionPathExists=/etc/surface-onboarding/approved-web-identity: removing the marker blocks it.
# `revert` disables both again (they keep running until stopped).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
OPS=$OPT/ops
M=/etc/surface-onboarding/approved-web-identity
UNIT=surface-onboarding-dashboard
case "${1:-}" in
revert)
  systemctl disable $UNIT nginx
  echo "enabled at boot: $(systemctl is-enabled $UNIT nginx | tr '\n' ' ')"
  exit 0;;
"") ;;
*) echo "usage: sh _step5a_vm_enable_at_boot.sh [revert]"; exit 1;;
esac

echo "== 1. preconditions (marker, unit, nginx config, loopback-only listeners) =="
[ -f "$M" ] || { echo "marker missing: stop here"; exit 1; }
[ -f /etc/systemd/system/$UNIT.service ] || { echo "unit missing: stop here"; exit 1; }
nginx -t
if ss -ltn | grep -E ':8000|:8443' | grep -v -E '127\.0\.0\.1:(8000|8443)'; then echo "a listener is not loopback-only: stop here"; exit 1; fi
echo ok

echo "== 2. enable at boot =="
systemctl enable $UNIT nginx
echo "enabled at boot: $(systemctl is-enabled $UNIT nginx | tr '\n' ' ')"
echo "active now:      $(systemctl is-active $UNIT nginx | tr '\n' ' ')"

echo "== 3. durable control scripts in $OPS (root-owned, 0755) =="
install -d -o root -g root -m 0755 "$OPS"
for f in /tmp/step2c.sh:pilot_start_stop.sh /tmp/r24-upload/_step3e_vm_activate_r24.sh:activate_release.sh; do
  src=${f%%:*}; dst=$OPS/${f##*:}
  if [ -f "$src" ]; then install -o root -g root -m 0755 "$src" "$dst"; echo "installed $dst"; else echo "skipped (not found): $src"; fi
done
ls -l "$OPS"
echo "after a reboot: systemctl is-active surface-onboarding-dashboard nginx   (expect active active)"
echo "turn the pilot off for good: sudo sh $OPS/pilot_start_stop.sh stop; sudo sh <this script> revert"
