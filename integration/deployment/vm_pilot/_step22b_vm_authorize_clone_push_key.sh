#!/bin/sh
# REVIEW ARTIFACT - Step 22B (operator-run on the VM as root):
#   sudo sh /tmp/step22b.sh /tmp/clone_push.pub <FROM>          add the key and create the approval marker
#   sudo sh /tmp/step22b.sh hint                                print recent source addresses of workato logins (read-only)
#   sudo sh /tmp/step22b.sh /tmp/clone_push.pub revert          remove the key line and the marker (the push stops at once)
# Interim clone push (docs/51). Adds ONE public key (the private half never leaves the owner's PC) to workato's authorized_keys,
# restricted to the forced command /usr/local/sbin/surface-clone-push-gate, `restrict` (no pty, no port, agent or X11 forwarding,
# no user rc) and from=<FROM> (the PC's VPN address or range, e.g. 10.8.0.5 or 10.8.0.0/24). Tagged `surface-clone-push` so revert
# removes exactly that line. Creating /etc/surface-onboarding/approved-clone-push IS the owner's approval: without it the gate refuses.
# Run ONLY after step 22A and your own go. No sshd change, no restart.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
USER_NAME=workato
HOME_DIR=$(getent passwd "$USER_NAME" | cut -d: -f6)
AK="$HOME_DIR/.ssh/authorized_keys"
TAG=surface-clone-push
MARKER=/etc/surface-onboarding/approved-clone-push
PUB=${1:-}

if [ "$PUB" = hint ]; then
  echo "recent workato logins (the source address is the PC's VPN address; use it as FROM):"
  last -i -n 15 "$USER_NAME" 2>/dev/null | head -n 15 || true
  exit 0
fi
[ -n "$PUB" ] || { echo "usage: sh step22b.sh /tmp/clone_push.pub <FROM> | hint | /tmp/clone_push.pub revert"; exit 1; }

if [ "${2:-}" = revert ]; then
  [ -f "$AK" ] && { grep -v " $TAG\$" "$AK" > "$AK.new" || true; cat "$AK.new" > "$AK"; rm -f "$AK.new"; }
  rm -f "$MARKER"
  N=$(grep -c . "$AK" 2>/dev/null || true)
  echo "removed lines tagged $TAG and the approval marker; remaining keys: ${N:-0}"; exit 0
fi

FROM=${2:-}
echo "== 1. checks =="
[ -f /usr/local/sbin/surface-clone-push-gate ] && [ -f /etc/sudoers.d/surface-clone-push ] || { echo "run step 22A first: stop here"; exit 1; }
# FROM: ONE IPv4 address or IPv4 range with a prefix of /16 or longer, or one IPv6 address/range with /48 or longer. Never any-address.
fail_from() { echo "FROM must be the PC's VPN address or a small range (IPv4 /16 or longer, IPv6 /48 or longer), e.g. 10.8.0.5 or 10.8.0.0/24: stop here"; exit 1; }
case "$FROM" in
  *:*)
    printf '%s' "$FROM" | grep -E -q '^[0-9A-Fa-f:]{3,39}(/[0-9]{1,3})?$' || fail_from
    case "$FROM" in ::|::/*|::0/*) fail_from;; esac
    PFX=${FROM#*/}; [ "$PFX" != "$FROM" ] && { [ "$PFX" -ge 48 ] && [ "$PFX" -le 128 ] || fail_from; } ;;
  *)
    printf '%s' "$FROM" | grep -E -q '^[0-9]{1,3}(\.[0-9]{1,3}){3}(/[0-9]{1,2})?$' || fail_from
    ADDR=${FROM%%/*}
    [ "$ADDR" != 0.0.0.0 ] || fail_from
    for OCT in $(printf '%s' "$ADDR" | tr . ' '); do [ "$OCT" -le 255 ] || fail_from; done
    PFX=${FROM#*/}; [ "$PFX" != "$FROM" ] && { [ "$PFX" -ge 16 ] && [ "$PFX" -le 32 ] || fail_from; } ;;
esac
[ -f "$PUB" ] || { echo "public key file missing: $PUB"; exit 1; }
[ "$(grep -c . "$PUB")" -eq 1 ] || { echo "the file must hold exactly one line"; exit 1; }
LINE=$(head -n 1 "$PUB" | tr -d '\r')
echo "$LINE" | grep -E -q '^ssh-ed25519 AAAA[A-Za-z0-9+/=]{20,} [A-Za-z0-9._@-]{1,64}$' || { echo "not a plain ssh-ed25519 public key line: stop here"; exit 1; }
KEY=$(echo "$LINE" | cut -d' ' -f1,2)
sshd -T 2>/dev/null | grep -i -q '^pubkeyauthentication yes' || { echo "public-key login is disabled on this VM: stop here"; exit 1; }
if [ -f "$AK" ] && grep -F "$KEY" "$AK" | grep -v " $TAG\$" | grep -q .; then echo "this key is already authorized WITHOUT the $TAG restrictions: remove that line by hand, stop here"; exit 1; fi

echo "== 2. add the restricted key (a re-run replaces our own line, so a changed FROM takes effect) =="
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0700 "$HOME_DIR/.ssh"
touch "$AK"; chown "$USER_NAME:$USER_NAME" "$AK"; chmod 0600 "$AK"
grep -v " $TAG\$" "$AK" > "$AK.new" || true; cat "$AK.new" > "$AK"; rm -f "$AK.new"
printf 'command="/usr/local/sbin/surface-clone-push-gate",from="%s",restrict %s %s\n' "$FROM" "$KEY" "$TAG" >> "$AK"
tail -n 1 "$AK" | cut -c1-150

echo "== 3. approval marker (the owner's go) =="
install -d -m 0755 /etc/surface-onboarding
printf 'approved by the owner for the interim clone push, %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$MARKER"
chmod 0644 "$MARKER"
N=$(grep -c . "$AK" 2>/dev/null || true)
echo "authorized_keys lines: ${N:-0}"
echo "remove later: sudo sh /tmp/step22b.sh /tmp/clone_push.pub revert"
