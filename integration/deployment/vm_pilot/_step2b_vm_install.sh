#!/bin/sh
# REVIEW ARTIFACT - Step 2B (operator-run on the VM as root: sudo sh /tmp/step2b.sh).
# Points `app` at the staged r22, installs the dashboard unit (PUBLIC_ORIGIN=https://localhost:8443) and the loopback-only
# tunnel nginx site (owner identity filled in), validates nginx config, reloads systemd. Starts and enables NOTHING and
# does NOT create /etc/surface-onboarding/approved-web-identity (the unit stays blocked by ConditionPathExists).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
OPT=/opt/surface-onboarding
NEW=$OPT/app-r22-staged-20261009
PREV=$OPT/app-prev-20260910
OWNER=milton.stevenson@pentera.io
T=$NEW/integration/deployment/vm_pilot
[ -d "$NEW" ] || { echo "missing $NEW"; exit 1; }

echo "== 1. refuse if anything uses the current app tree =="
if pgrep -af "$OPT/app/" | grep -v "$0"; then echo "processes use $OPT/app: stop here"; exit 1; fi
systemctl list-units --all 'surface-onboarding*' --no-legend || true

echo "== 2. switch app to r22 (old tree kept as $PREV) =="
if [ -L "$OPT/app" ]; then echo "app is already a symlink -> $(readlink "$OPT/app")"; exit 1; fi
[ ! -e "$PREV" ] || { echo "$PREV exists"; exit 1; }
mv "$OPT/app" "$PREV"
ln -s "$NEW" "$OPT/app"
ls -ld "$OPT/app" "$PREV"

echo "== 3. dashboard unit (not enabled, not started) =="
sed 's#^Environment=SURFACE_ONBOARDING_PUBLIC_ORIGIN=.*#Environment=SURFACE_ONBOARDING_PUBLIC_ORIGIN=https://localhost:8443#' \
  "$T/surface-onboarding-dashboard.service.template" > /etc/systemd/system/surface-onboarding-dashboard.service
chmod 0644 /etc/systemd/system/surface-onboarding-dashboard.service
grep -n "PUBLIC_ORIGIN\|ConditionPathExists\|USERS_FILE\|PROXY_SECRET" /etc/systemd/system/surface-onboarding-dashboard.service
systemctl daemon-reload

echo "== 4. loopback-only tunnel nginx site (not started) =="
sed "s#<OWNER_EMAIL>#$OWNER#" "$T/nginx-surface-onboarding-tunnel.conf.template" \
  > /etc/nginx/sites-available/surface-onboarding-tunnel.conf
chmod 0644 /etc/nginx/sites-available/surface-onboarding-tunnel.conf
ln -sfn /etc/nginx/sites-available/surface-onboarding-tunnel.conf /etc/nginx/sites-enabled/surface-onboarding-tunnel.conf
! grep -n '<OWNER_EMAIL>' /etc/nginx/sites-available/surface-onboarding-tunnel.conf
grep -n "listen\|X-Forwarded-Email" /etc/nginx/sites-available/surface-onboarding-tunnel.conf
nginx -t

echo "== 5. state (expect: unit inactive+disabled, nginx inactive+disabled, no marker) =="
systemctl is-enabled surface-onboarding-dashboard nginx || true
systemctl is-active surface-onboarding-dashboard nginx || true
ls -l /etc/surface-onboarding/approved-web-identity 2>&1 || true
