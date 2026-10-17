#!/bin/sh
# REVIEW ARTIFACT - Step 23B (operator-run on the VM as root): sudo sh /tmp/r34-upload/_step23b_vm_activate_r34.sh [rollback]
# Points `app` at the staged, tested r34 and restarts ONLY the dashboard if the pilot is currently running; if the pilot
# is stopped it stays stopped. Does not touch nginx, the approved-web-identity marker, /etc/surface-onboarding,
# snapshots, boot enablement, the firewall or any external system. `rollback` points `app` back at r33.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
R33=$OPT/app-r33-staged-20261016
R34=$OPT/app-r34-staged-20261017
UNIT=surface-onboarding-dashboard
case "${1:-}" in
"") FROM=$R33; TO=$R34;;
rollback) FROM=$R34; TO=$R33;;
*) echo "usage: sh _step23b_vm_activate_r34.sh [rollback]"; exit 1;;
esac
[ -d "$TO/integration" ] || { echo "missing $TO"; exit 1; }
[ -L "$OPT/app" ] && [ "$(readlink "$OPT/app")" = "$FROM" ] || { echo "app is not -> $FROM (is: $(readlink "$OPT/app" || echo not-a-symlink)): stop here"; exit 1; }

echo "== 1. state before =="
ls -ld "$OPT/app"; systemctl is-active $UNIT nginx || true
WAS_ACTIVE=no; systemctl is-active --quiet $UNIT && WAS_ACTIVE=yes

echo "== 2. repoint app -> $TO =="
ln -sfn "$TO" "$OPT/app.new" && mv -T "$OPT/app.new" "$OPT/app"
ls -ld "$OPT/app"

if [ "$WAS_ACTIVE" = yes ]; then
  echo "== 3. restart the dashboard only =="
  systemctl restart $UNIT
  sleep 3
  systemctl is-active $UNIT || { journalctl -u $UNIT -n 25 --no-pager; echo "FAILED: run this script with: rollback"; exit 1; }
  echo "== 4. checks (status codes only; listeners expect only 127.0.0.1) =="
  ss -ltnp | grep -E ':8000|:8443' || true
  curl -sk -o /dev/null -w "GET / -> %{http_code} (expect 200)\n" https://localhost:8443/
  curl -sk -o /dev/null -w "POST / -> %{http_code} (expect 403/405)\n" -X POST https://localhost:8443/
  curl -s -o /dev/null -w "direct dashboard without secret -> %{http_code} (expect 4xx)\n" http://127.0.0.1:8000/
  curl -sk https://localhost:8443/ | grep -o -E 'Data as of[^<]{0,40}|Page rendered at|Read from Salesforce at' | sort | uniq -c || true
else
  echo "== 3. pilot was stopped: left stopped (start it with: sudo sh /tmp/step2c.sh start) =="
fi
echo "enabled at boot: $(systemctl is-enabled $UNIT nginx | tr '\n' ' ')"
echo "rollback: sudo sh /tmp/r34-upload/_step23b_vm_activate_r34.sh rollback"
