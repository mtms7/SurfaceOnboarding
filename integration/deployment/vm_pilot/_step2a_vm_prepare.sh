#!/bin/sh
# REVIEW ARTIFACT - Step 2A (operator-run on the VM as root: sudo sh /tmp/step2a.sh). Review artifact; creates nothing that serves traffic.
# Installs nginx but leaves it STOPPED and DISABLED, creates root-owned config dirs, secrets (never printed) and the
# pilot self-signed certificate. Does NOT create approved-web-identity, does NOT install any site, does NOT start anything.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

echo "== 1. nginx (apt) =="
apt-get install -y nginx
# The Ubuntu package starts a default site on 0.0.0.0:80: stop and disable it right away.
systemctl disable --now nginx
rm -f /etc/nginx/sites-enabled/default
NGINX_GROUP=www-data
getent group "$NGINX_GROUP" >/dev/null || { echo "group $NGINX_GROUP missing"; exit 1; }

echo "== 2. config dir, proxy secret, snapshot key (values never printed) =="
install -d -m 0750 -o root -g surface-onboarding /etc/surface-onboarding
umask 0137
[ -e /etc/surface-onboarding/proxy-secret ] || openssl rand -hex 32 > /etc/surface-onboarding/proxy-secret
[ -e /etc/surface-onboarding/snapshot.key ] || openssl rand -hex 32 > /etc/surface-onboarding/snapshot.key
chown root:surface-onboarding /etc/surface-onboarding/proxy-secret /etc/surface-onboarding/snapshot.key
chmod 0640 /etc/surface-onboarding/proxy-secret /etc/surface-onboarding/snapshot.key

printf 'set $surface_proxy_secret "%s";\n' "$(cat /etc/surface-onboarding/proxy-secret)" \
  > /etc/nginx/surface-onboarding-proxy-secret.conf
chown root:"$NGINX_GROUP" /etc/nginx/surface-onboarding-proxy-secret.conf
chmod 0640 /etc/nginx/surface-onboarding-proxy-secret.conf

echo "== 3. users file (owner as plain VIEWER for the tunnel interim) =="
umask 0027
[ -e /etc/surface-onboarding/users.txt ] || echo "milton.stevenson@pentera.io" > /etc/surface-onboarding/users.txt
chown root:surface-onboarding /etc/surface-onboarding/users.txt
chmod 0640 /etc/surface-onboarding/users.txt

echo "== 4. pilot self-signed certificate (SAN: localhost, 127.0.0.1, VM IP) =="
install -d -m 0750 -o root -g "$NGINX_GROUP" /etc/nginx/surface-onboarding/tls
if [ ! -e /etc/nginx/surface-onboarding/tls/surface-onboarding.crt ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days 90 -nodes \
    -subj "/CN=localhost/O=Surface onboarding pilot" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:172.26.37.20" \
    -keyout /etc/nginx/surface-onboarding/tls/surface-onboarding.key \
    -out    /etc/nginx/surface-onboarding/tls/surface-onboarding.crt
fi
chown root:"$NGINX_GROUP" /etc/nginx/surface-onboarding/tls/surface-onboarding.key
chmod 0640 /etc/nginx/surface-onboarding/tls/surface-onboarding.key
chmod 0644 /etc/nginx/surface-onboarding/tls/surface-onboarding.crt

echo "== 5. result (names, modes, no values) =="
ls -l /etc/surface-onboarding /etc/nginx/surface-onboarding-proxy-secret.conf /etc/nginx/surface-onboarding/tls
nginx -v 2>&1
systemctl is-enabled nginx || true
systemctl is-active nginx || true
echo "length proxy-secret: $(wc -c < /etc/surface-onboarding/proxy-secret)  snapshot.key: $(wc -c < /etc/surface-onboarding/snapshot.key)"
