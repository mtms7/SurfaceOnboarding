#!/bin/sh
# REVIEW ARTIFACT - Step 8B (operator-run on the VM as root: sudo sh /tmp/step8b.sh <EXPECTED_SHA256>). Spike for docs/43 section 8.
# Installs the Salesforce CLI downloaded by step 8A, ONLY if its SHA-256 equals the hash you pass (the one you compared on the
# GitHub releases page). Unpacks as root into /opt/surface-onboarding/sf-cli (root-owned, world-readable, not writable by the
# service user), creates the in-memory session folder /run/surface-onboarding-sf (tmpfs, 0700), and runs `sf --version` as the
# service user with its home in memory. Starts no service, creates no approval marker, signs in to nothing, stores no credential.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
EXPECTED=$(printf '%s' "${1:-}" | tr 'a-f' 'A-F')
printf '%s' "$EXPECTED" | grep -Eq '^[0-9A-F]{64}$' || { echo "usage: sh step8b.sh <64-hex sha256 from the releases page>"; exit 1; }
FILE=/root/sf-cli-download/sf-linux-x64.tar.xz
DEST=/opt/surface-onboarding/sf-cli
SESSION=/run/surface-onboarding-sf
RUNAS=surface-onboarding
[ -f "$FILE" ] || { echo "missing $FILE: run step 8A first"; exit 1; }
[ ! -e "$DEST" ] || { echo "$DEST already exists: stop here (remove it deliberately to reinstall)"; exit 1; }

echo "== 1. verify the hash you compared =="
ACTUAL=$(sha256sum "$FILE" | cut -d' ' -f1 | tr 'a-f' 'A-F')
echo "actual   $ACTUAL"; echo "expected $EXPECTED"
[ "$ACTUAL" = "$EXPECTED" ] || { echo "HASH MISMATCH: nothing was installed"; exit 1; }
echo OK
if tar -tJf "$FILE" | grep -E '(^/|(^|/)\.\.(/|$))'; then echo "unsafe member path: nothing was installed"; exit 1; fi

echo "== 2. unpack as root into $DEST (not owned by the service user) =="
install -d -m 0755 -o root -g root "$DEST"
tar -xJf "$FILE" -C "$DEST" --strip-components 1 --no-same-owner
chown -R root:root "$DEST"
chmod -R go-w "$DEST"
ls -l "$DEST/bin/sf"

echo "== 3. in-memory session folder (tmpfs under /run, private to the service user) =="
install -d -m 0700 -o "$RUNAS" -g "$RUNAS" "$SESSION"
ls -ld "$SESSION"
df -hT "$SESSION" | sed -n '1,2p'

echo "== 4. sf --version as the service user, home in memory, telemetry and updates off =="
sudo -u "$RUNAS" env HOME="$SESSION" SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true SF_AUTOUPDATE_DISABLE=true \
  SFDX_AUTOUPDATE_DISABLE=true SF_SKIP_NEW_VERSION_CHECK=true "$DEST/bin/sf" --version

echo "== 5. state =="
ls "$SESSION" -la | sed -n '1,5p'
echo "installed; nothing signed in, no service started. Next: sudo sh /tmp/step8c.sh <your My Domain login URL>"
