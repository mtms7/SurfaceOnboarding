#!/bin/sh
# REVIEW ARTIFACT - Step 28B (operator-run on the VM as root): sudo sh /tmp/_step28b_vm_sf_refresh_cadence.sh <minutes 10-240> | reset | show
# Owner decision 2026-10-27: each Salesforce refresh costs about 3 minutes of CPU (about 390 CLI reads), so every 10 minutes keeps the
# VM busy a third of the time while onboarding decisions move in hours. This changes ONLY how often the refresh timer fires, with a
# systemd drop-in (/etc/systemd/system/surface-onboarding-sf-refresh.timer.d/interval.conf); the unit file, the approval marker, the
# sign-in session and every snapshot are untouched. `reset` removes the drop-in (back to the 10-minute default of the unit).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
TIMER=surface-onboarding-sf-refresh.timer
DROPIN_DIR=/etc/systemd/system/$TIMER.d
DROPIN=$DROPIN_DIR/interval.conf
ARG=${1:-}
case "$ARG" in
show) ;;
reset) rm -f "$DROPIN"; rmdir "$DROPIN_DIR" 2>/dev/null || true; systemctl daemon-reload; systemctl restart "$TIMER";;
*)
  printf '%s' "$ARG" | grep -Eq '^[0-9]{2,3}$' && [ "$ARG" -ge 10 ] && [ "$ARG" -le 240 ] || { echo "usage: sh _step28b_vm_sf_refresh_cadence.sh <minutes 10-240> | reset | show"; exit 1; }
  [ -f "/etc/systemd/system/$TIMER" ] || { echo "the refresh timer is not installed (step 28A first): stop here"; exit 1; }
  install -d -m 0755 -o root -g root "$DROPIN_DIR"
  printf '[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=%smin\n' "$ARG" > "$DROPIN"
  chmod 0644 "$DROPIN"
  systemctl daemon-reload
  systemctl restart "$TIMER";;
esac
echo "drop-in: $([ -f "$DROPIN" ] && sed -n '3p' "$DROPIN" || echo none)"
systemctl is-enabled "$TIMER"; systemctl is-active "$TIMER"
systemctl list-timers "$TIMER" --no-pager | sed -n '1,3p'
