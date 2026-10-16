#!/bin/sh
# REVIEW ARTIFACT - Step 16B (operator-run on the VM as root):
#   sudo sh /tmp/_step16b_vm_lan_site.sh [allowed-cidr ...]      default allowed range: 10.213.213.1/32 (what the VM saw for the VPN)
#   sudo sh /tmp/_step16b_vm_lan_site.sh revert
# Needs /tmp/nginx-surface-onboarding-lan.conf.template next to it. Run step 16A (add at least one person) FIRST.
# THIS IS THE EXPOSURE STEP: after the nginx reload the dashboard also answers on 172.26.37.20:8443 for the allowed
# range, behind basic auth. Installs only: the nginx site, /etc/nginx/surface-onboarding/lan-allow.conf, a symlink in
# sites-enabled, and a reload. Does NOT touch the loopback tunnel site, the dashboard unit or its origin, the marker,
# the certificate (checked, not changed), users.txt, ufw (step 16C) or any other service. `revert` removes all of it.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
SITE=/etc/nginx/sites-available/surface-onboarding-lan.conf
LINK=/etc/nginx/sites-enabled/surface-onboarding-lan.conf
ALLOW=/etc/nginx/surface-onboarding/lan-allow.conf
HTP=/etc/nginx/surface-onboarding/htpasswd
CRT=/etc/nginx/surface-onboarding/tls/surface-onboarding.crt
TPL=/tmp/nginx-surface-onboarding-lan.conf.template
VMIP=172.26.37.20

if [ "${1:-}" = revert ]; then
  rm -f "$LINK" "$SITE" "$ALLOW"
  nginx -t && systemctl reload nginx
  echo "reverted: listeners now:"; ss -ltn | grep -E ':8443' || true
  exit 0
fi

echo "== 1. preconditions =="
[ -f "$TPL" ] || { echo "missing $TPL: upload it first"; exit 1; }
[ -f "$HTP" ] && [ "$(grep -c . "$HTP")" -ge 1 ] || { echo "no password entry: run step 16A add first"; exit 1; }
[ -f /etc/nginx/surface-onboarding-proxy-secret.conf ] || { echo "proxy-secret include missing: stop here"; exit 1; }
[ -f /etc/surface-onboarding/approved-web-identity ] || { echo "approval marker missing: stop here"; exit 1; }
systemctl is-active --quiet surface-onboarding-dashboard && systemctl is-active --quiet nginx || { echo "dashboard or nginx not active: stop here"; exit 1; }
ip -4 addr show | grep -q "inet $VMIP/" || { echo "$VMIP is not an address of this VM: stop here"; exit 1; }
echo "certificate (subject alternative names and expiry):"
openssl x509 -in "$CRT" -noout -ext subjectAltName -enddate
openssl x509 -in "$CRT" -noout -ext subjectAltName | grep -q "IP Address:$VMIP" || { echo "certificate has no IP:$VMIP: stop here"; exit 1; }
openssl x509 -in "$CRT" -noout -checkend 604800 >/dev/null || { echo "certificate expires within 7 days or is expired: renew first"; exit 1; }
echo "users with a password:"; cut -d: -f1 "$HTP" | sed 's/^/  /'
[ ! -e "$SITE" ] || { echo "$SITE already exists: run revert first"; exit 1; }

echo "== 2. allowed client range =="
[ "$#" -ge 1 ] || set -- 10.213.213.1/32
for c in "$@"; do
  echo "$c" | grep -E -q '^([0-9]{1,3}\.){3}[0-9]{1,3}/(8|9|1[0-9]|2[0-9]|3[0-2])$' || { echo "not an IPv4 CIDR: $c"; exit 1; }
  case "$c" in 0.0.0.0/*) echo "refusing 0.0.0.0/x"; exit 1;; esac
done
: > "$ALLOW"
for c in "$@"; do echo "allow $c;" >> "$ALLOW"; done
echo "deny all;" >> "$ALLOW"
chmod 0644 "$ALLOW"; cat "$ALLOW"

echo "== 3. install the site and check the configuration =="
install -o root -g root -m 0644 "$TPL" "$SITE"
sed -i "s#listen 172.26.37.20:8443#listen $VMIP:8443#; s#server_name 172.26.37.20;#server_name $VMIP;#" "$SITE"
ln -sfn "$SITE" "$LINK"
if ! nginx -t; then rm -f "$LINK" "$SITE"; echo "nginx -t failed: site removed, nothing reloaded"; exit 1; fi

echo "== 4. reload nginx (listeners change only now) =="
systemctl reload nginx
sleep 2
ss -ltn | grep -E ':8443|:8000'

echo "== 5. checks (status codes only) =="
curl -sk -m 10 -o /dev/null -w "tunnel path https://localhost:8443/ -> %{http_code} (expect 200)\n" https://localhost:8443/
# Called from the VM itself, whose own address is not in the allowed range: 403 proves the network check; the 401 login prompt is seen from an allowed client.
curl -sk -m 10 -o /dev/null -w "LAN path from the VM itself (outside the allowed range) -> %{http_code} (expect 403)\n" https://$VMIP:8443/
curl -s  -m 10 -o /dev/null -w "direct dashboard without secret -> %{http_code} (expect 4xx)\n" http://127.0.0.1:8000/
echo "browser test from the PC on the VPN: https://$VMIP:8443/  (accept the certificate warning, sign in with the email and password)"
echo "undo: sudo sh /tmp/_step16b_vm_lan_site.sh revert"
