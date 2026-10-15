#!/bin/sh
# REVIEW ARTIFACT - Step 6A (operator-run on the VM as root: sudo sh /tmp/step6a.sh).
# Prepares the Salesforce reader identity material ONLY (docs/43 part A, docs/44): creates the two credential groups, generates
# a self-signed RSA key pair ON the VM (the private key never leaves it), and copies the PUBLIC certificate to a place you
# can scp from. Salesforce accepts a self-signed certificate for the JWT bearer flow by design (no CA is involved).
# Does NOT create sf-reader.json, any approval marker, any unit or timer; contacts no external system; prints no key material.
# Safe to re-run: an existing key or certificate is kept and never overwritten.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
DIR=/etc/surface-onboarding
KEY=$DIR/sf-reader.pem
CRT_ROOT=/root/sf-reader.crt
CRT_OUT=/tmp/sf-reader.crt
[ -d "$DIR" ] || { echo "$DIR missing: stop here"; exit 1; }

echo "== 1. credential groups (system groups; the dashboard unit is NOT a member) =="
for group in surface-sf-reader surface-redash-reader; do
  getent group "$group" >/dev/null && echo "exists: $group" || { groupadd --system "$group"; echo "created: $group"; }
done

echo "== 2. key pair on the VM (kept if it already exists) =="
if [ -e "$KEY" ] || [ -e "$CRT_ROOT" ]; then
  echo "key or certificate already exists: keeping both"
else
  umask 0137
  openssl req -x509 -newkey rsa:3072 -sha256 -days 730 -nodes -subj "/CN=surface-onboarding-vm-reader" \
    -keyout "$KEY" -out "$CRT_ROOT" 2>/dev/null
fi
chown root:surface-sf-reader "$KEY"
chmod 0640 "$KEY"
chown root:root "$CRT_ROOT"
chmod 0644 "$CRT_ROOT"

echo "== 3. public certificate for the Salesforce admin =="
install -m 0644 -o root -g root "$CRT_ROOT" "$CRT_OUT"
openssl x509 -in "$CRT_ROOT" -noout -subject -dates -fingerprint -sha256
head -1 "$CRT_OUT"
grep -c "PRIVATE" "$CRT_OUT" | sed 's/^/private-key lines in the certificate file (expect 0): /'

echo "== 4. result (names, owners, modes only) =="
ls -l "$KEY" "$CRT_ROOT" "$CRT_OUT"
getent group surface-sf-reader surface-redash-reader | cut -d: -f1,3
id -nG surface-onboarding
echo "next: scp the certificate to your desktop:  scp workato@172.26.37.20:/tmp/sf-reader.crt .   then send it with the request (docs/44)"
