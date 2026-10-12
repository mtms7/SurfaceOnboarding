#!/bin/sh
# REVIEW ARTIFACT - Step 2C (operator-run on the VM as root): sudo sh /tmp/step2c.sh start | stop
# start: creates the approved-web-identity marker (operator-approved TEMPORARY loopback-only listener), starts the
#        dashboard (127.0.0.1:8000) and nginx (127.0.0.1:8443), checks both are loopback-only and answer.
#        Nothing is enabled at boot, nothing is exposed off the VM.
# stop:  stops both and removes the marker (the unit is blocked again).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
M=/etc/surface-onboarding/approved-web-identity
case "${1:-}" in
start)
  install -m 0640 -o root -g surface-onboarding /dev/null "$M"
  systemctl start surface-onboarding-dashboard
  sleep 3
  systemctl is-active surface-onboarding-dashboard || { journalctl -u surface-onboarding-dashboard -n 25 --no-pager; exit 1; }
  nginx -t
  systemctl start nginx
  sleep 1
  echo "== listeners (expect only 127.0.0.1:8000 and 127.0.0.1:8443; no 0.0.0.0 / :::) =="
  ss -ltnp | grep -E ':8000|:8443|:80 |:443 ' || true
  echo "== requests through the tunnel site (status codes only) =="
  curl -sk -o /dev/null -w "GET / -> %{http_code}\n" https://localhost:8443/
  curl -sk -o /dev/null -w "POST / -> %{http_code} (expect 403/405, never 2xx)\n" -X POST https://localhost:8443/
  curl -s -o /dev/null -w "direct dashboard without secret -> %{http_code} (expect 4xx)\n" http://127.0.0.1:8000/
  echo "enabled at boot: $(systemctl is-enabled surface-onboarding-dashboard nginx | tr '\n' ' ')"
  ;;
stop)
  systemctl stop nginx || true
  systemctl stop surface-onboarding-dashboard || true
  rm -f "$M"
  systemctl is-active nginx surface-onboarding-dashboard || true
  ls "$M" 2>&1 || true
  ;;
*) echo "usage: sh step2c.sh start|stop"; exit 1;;
esac
