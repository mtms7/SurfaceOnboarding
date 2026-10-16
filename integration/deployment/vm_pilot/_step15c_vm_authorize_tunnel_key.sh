#!/bin/sh
# REVIEW ARTIFACT - Step 15C (operator-run on the VM as root): sudo sh /tmp/step15c.sh /tmp/vm_tunnel.pub [revert]
# Lets the owner's desktop keep the SSH tunnel open WITHOUT typing a password: it adds ONE public key (the half that is
# safe to share; the private key never leaves the desktop) to /home/workato/.ssh/authorized_keys, restricted so that this key can ONLY
# forward to 127.0.0.1:8443 on the VM: no shell, no command (forced /bin/false), no pty, no agent/X11, no other port.
# Tagged `surface-vm-tunnel` so `revert` removes exactly that line. Changes nothing else; no restart; no sshd change.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
USER_NAME=workato
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
AK="$HOME_DIR/.ssh/authorized_keys"
TAG=surface-vm-tunnel
PUB=${1:-}
[ -n "$PUB" ] || { echo "usage: sh step15c.sh /tmp/vm_tunnel.pub [revert]"; exit 1; }

if [ "${2:-}" = revert ]; then
  [ -f "$AK" ] && { grep -v " $TAG\$" "$AK" > "$AK.new" || true; cat "$AK.new" > "$AK"; rm -f "$AK.new"; }
  echo "removed lines tagged $TAG; remaining keys: $(grep -c . "$AK" 2>/dev/null || echo 0)"; exit 0
fi

echo "== 1. checks =="
[ -f "$PUB" ] || { echo "public key file missing: $PUB"; exit 1; }
[ "$(grep -c . "$PUB")" -eq 1 ] || { echo "the file must hold exactly one line"; exit 1; }
LINE=$(head -n 1 "$PUB" | tr -d '\r')   # a key file written on Windows ends with CR LF
# Exactly: ssh-ed25519 <base64> <comment>; refuse anything with options, spaces beyond that, or a private key.
echo "$LINE" | grep -E -q '^ssh-ed25519 AAAA[A-Za-z0-9+/=]{20,} [A-Za-z0-9._@-]{1,64}$' || { echo "not a plain ssh-ed25519 public key line: stop here"; exit 1; }
KEY=$(echo "$LINE" | cut -d' ' -f1,2)
echo "$(sshd -T 2>/dev/null | grep -i '^pubkeyauthentication') (must be yes)"
sshd -T 2>/dev/null | grep -i -q '^pubkeyauthentication yes' || { echo "public-key login is disabled on this VM: stop here"; exit 1; }
if [ -f "$AK" ] && grep -q -F "$KEY" "$AK"; then echo "this key is already authorized: nothing to do"; exit 0; fi

echo "== 2. add the restricted key =="
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$HOME_DIR/.ssh"
touch "$AK"; chown "$USER_NAME:$USER_NAME" "$AK"; chmod 0600 "$AK"
printf 'command="/bin/false",restrict,port-forwarding,permitopen="127.0.0.1:8443" %s %s\n' "$KEY" "$TAG" >> "$AK"
tail -n 1 "$AK" | cut -c1-130
echo "authorized_keys lines: $(grep -c . "$AK")"
echo "remove later: sudo sh /tmp/step15c.sh /tmp/vm_tunnel.pub revert"
