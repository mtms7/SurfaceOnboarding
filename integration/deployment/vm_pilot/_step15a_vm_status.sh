#!/bin/sh
# REVIEW ARTIFACT - Step 15A (operator-run on the VM as root): sudo sh /tmp/step15a.sh
# READ-ONLY availability report for the 24/7 goal: uptime and last reboots, dashboard/nginx/validator/intake state,
# enabled-at-boot, restart policy (Restart=, StartLimit*), recent failures, listeners, local health codes.
# Changes nothing: no start/stop/restart/enable, no file written, no secret printed (status codes and unit properties only).
set -u
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

echo "== 1. uptime and reboots =="
uptime
who -b 2>/dev/null || true
last -x reboot 2>/dev/null | head -n 5 || true
[ -f /var/run/reboot-required ] && echo "reboot required: yes" || echo "reboot required: no"

echo "== 2. units: active / enabled =="
for u in surface-onboarding-dashboard nginx surface-onboarding-validator surface-onboarding-intake \
         surface-onboarding-sf-refresh.timer surface-onboarding-redash-collect.timer; do
  printf '%-45s active=%-10s enabled=%s\n' "$u" "$(systemctl is-active "$u" 2>&1)" "$(systemctl is-enabled "$u" 2>&1)"
done
systemctl list-units --all --no-legend 'surface-onboarding*' 2>/dev/null || true

echo "== 3. restart policy (dashboard, nginx) =="
for u in surface-onboarding-dashboard nginx; do
  echo "-- $u"
  systemctl show "$u" -p Restart -p RestartUSec -p StartLimitIntervalUSec -p StartLimitBurst \
    -p NRestarts -p ActiveEnterTimestamp -p ActiveState -p SubState -p Result 2>&1
  systemctl show "$u" -p DropInPaths 2>&1
done

echo "== 4. recent journal (dashboard: warnings and worse; last 15 lines) =="
journalctl -u surface-onboarding-dashboard -p warning -n 15 --no-pager 2>&1 || true
echo "== 4b. nginx last 5 lines =="
journalctl -u nginx -n 5 --no-pager 2>&1 || true

echo "== 5. listeners (expect only 127.0.0.1) =="
ss -ltn | grep -E ':8000|:8443' || echo "no listener on 8000/8443"

echo "== 6. local health (status codes only) =="
curl -sk -m 10 -o /dev/null -w "GET / via nginx -> %{http_code} (expect 200)\n" https://localhost:8443/ || echo "nginx path: no answer"
curl -s  -m 10 -o /dev/null -w "direct dashboard without secret -> %{http_code} (expect 4xx)\n" http://127.0.0.1:8000/ || echo "dashboard: no answer"

echo "== 7. disk and memory =="
df -h /var/lib/surface-onboarding /opt/surface-onboarding 2>/dev/null || true
free -m | head -n 2
echo "== done: nothing was changed =="
