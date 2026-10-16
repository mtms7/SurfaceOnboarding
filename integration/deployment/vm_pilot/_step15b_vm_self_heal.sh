#!/bin/sh
# REVIEW ARTIFACT - Step 15B (operator-run on the VM as root): sudo sh /tmp/step15b.sh [revert]
# Goal: the loopback-only pilot (dashboard + nginx) keeps itself running without anyone starting it.
# Installs ONLY:
#   1. systemd drop-ins: surface-onboarding-dashboard and nginx get Restart=always, RestartSec=5 and
#      StartLimitIntervalSec=0 (systemd never gives up after quick crashes; the 2026-10-16 status showed Restart=on-failure
#      with a 5-in-10-seconds limit for the dashboard and Restart=no for nginx).
#   2. /opt/surface-onboarding/ops/pilot_health.sh plus a oneshot service and a 2-minute timer: it asks https://localhost:8443/
#      for a status code only; no answer (000) or 5xx restarts the dashboard, and if still bad, nginx. Logged to the journal
#      (tag surface-onboarding-health). 2xx/3xx/4xx = leave everything alone.
# Does NOT touch: the approved-web-identity marker, unit files, nginx config, listeners, firewall, /etc/surface-onboarding,
# snapshots, the Workato agent or any external system. Does not restart the running dashboard or nginx now.
# `revert` removes the drop-ins, the timer, the service and the health script.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPS=/opt/surface-onboarding/ops
D1=/etc/systemd/system/surface-onboarding-dashboard.service.d
D2=/etc/systemd/system/nginx.service.d
SVC=/etc/systemd/system/surface-onboarding-health.service
TMR=/etc/systemd/system/surface-onboarding-health.timer
case "${1:-}" in
revert)
  systemctl disable --now surface-onboarding-health.timer 2>/dev/null || true
  rm -f "$D1/10-self-heal.conf" "$D2/10-self-heal.conf" "$SVC" "$TMR" "$OPS/pilot_health.sh"
  rmdir "$D1" "$D2" 2>/dev/null || true
  systemctl daemon-reload
  echo "reverted"; exit 0;;
"") ;;
*) echo "usage: sh step15b.sh [revert]"; exit 1;;
esac

echo "== 1. preconditions =="
[ -f /etc/systemd/system/surface-onboarding-dashboard.service ] || { echo "dashboard unit missing: stop here"; exit 1; }
[ -f /etc/surface-onboarding/approved-web-identity ] || { echo "approval marker missing: stop here"; exit 1; }
systemctl is-active --quiet surface-onboarding-dashboard && systemctl is-active --quiet nginx || { echo "dashboard or nginx not active now: stop here (start the pilot first)"; exit 1; }
echo ok

echo "== 2. restart drop-ins =="
for d in "$D1" "$D2"; do
  install -d -o root -g root -m 0755 "$d"
  cat > "$d/10-self-heal.conf" <<'EOF'
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=5
EOF
  chmod 0644 "$d/10-self-heal.conf"
done

echo "== 3. health script (status code only, no secret) =="
install -d -o root -g root -m 0755 "$OPS"
cat > "$OPS/pilot_health.sh" <<'EOF'
#!/bin/sh
# Restart the pilot only when it does not answer (000) or answers 5xx. Never prints content.
code=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost:8443/ 2>/dev/null || true)
case "$code" in
  2??|3??|4??) exit 0;;
esac
logger -t surface-onboarding-health "GET / -> ${code:-none}: restarting dashboard"
systemctl restart surface-onboarding-dashboard
sleep 8
code=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost:8443/ 2>/dev/null || true)
case "$code" in
  2??|3??|4??) logger -t surface-onboarding-health "recovered after dashboard restart ($code)"; exit 0;;
esac
logger -t surface-onboarding-health "still ${code:-none}: restarting nginx"
systemctl restart nginx
sleep 3
code=$(curl -sk -m 10 -o /dev/null -w '%{http_code}' https://localhost:8443/ 2>/dev/null || true)
logger -t surface-onboarding-health "after nginx restart: ${code:-none}"
EOF
chown root:root "$OPS/pilot_health.sh"; chmod 0755 "$OPS/pilot_health.sh"

cat > "$SVC" <<'EOF'
[Unit]
Description=Surface onboarding pilot health check (restarts the loopback pilot if it does not answer)
After=surface-onboarding-dashboard.service nginx.service

[Service]
Type=oneshot
ExecStart=/opt/surface-onboarding/ops/pilot_health.sh
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
EOF
cat > "$TMR" <<'EOF'
[Unit]
Description=Surface onboarding pilot health check every 2 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
AccuracySec=15s

[Install]
WantedBy=timers.target
EOF
chmod 0644 "$SVC" "$TMR"

echo "== 4. activate (daemon-reload; the running services are NOT restarted) =="
systemctl daemon-reload
systemctl enable --now surface-onboarding-health.timer

echo "== 5. result =="
systemctl show surface-onboarding-dashboard nginx -p Id -p Restart -p StartLimitIntervalUSec
systemctl is-active surface-onboarding-dashboard nginx
systemctl is-enabled surface-onboarding-dashboard nginx surface-onboarding-health.timer
systemctl list-timers surface-onboarding-health.timer --no-pager
echo "test a recovery (optional, brief outage): sudo systemctl kill -s KILL surface-onboarding-dashboard ; sleep 8 ; systemctl is-active surface-onboarding-dashboard"
