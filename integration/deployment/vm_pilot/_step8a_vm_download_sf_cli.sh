#!/bin/sh
# REVIEW ARTIFACT - Step 8A (operator-run on the VM as root: sudo sh /tmp/step8a.sh). Spike for docs/43 section 8.
# DOWNLOAD ONLY. Fetches the official Salesforce CLI Linux tarball into a root-only folder, prints its size, SHA-256 and the
# version it contains (read from inside the archive without unpacking or running anything), and stops. Nothing is installed or
# executed. The unversioned download URL has no published checksum, so YOU compare the printed SHA-256 with the hash GitHub shows
# for the same version (https://github.com/salesforcecli/cli/releases), then run step 8B with that hash.
# Source: https://developer.salesforce.com/media/salesforce-cli/sf/channels/stable/sf-linux-x64.tar.xz (HTTPS only, TLS 1.2+).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
URL=https://developer.salesforce.com/media/salesforce-cli/sf/channels/stable/sf-linux-x64.tar.xz
DIR=/root/sf-cli-download
FILE=$DIR/sf-linux-x64.tar.xz
install -d -m 0700 -o root -g root "$DIR"
rm -f "$FILE"

echo "== 1. download (HTTPS only; the file is only stored, never executed) =="
curl -fSL --proto '=https' --tlsv1.2 --max-filesize 600000000 --connect-timeout 20 --max-time 900 -o "$FILE" "$URL"
chmod 0600 "$FILE"

echo "== 2. what we got =="
ls -l "$FILE"
echo "sha256: $(sha256sum "$FILE" | cut -d' ' -f1)"
xz -t "$FILE" && echo "xz integrity: ok"

echo "== 3. members (read-only listing; expect one top folder, no absolute or .. paths) =="
if tar -tJf "$FILE" | grep -E '(^/|(^|/)\.\.(/|$))'; then echo "unsafe member path: do NOT continue"; exit 1; fi
echo "no unsafe paths; members: $(tar -tJf "$FILE" | wc -l)"
tar -tJf "$FILE" | sed -n '1,3p'

echo "== 4. version inside the archive (read from package.json, nothing is run) =="
tar -xJOf "$FILE" sf/package.json 2>/dev/null | grep -m1 '"version"' || echo "version not found at sf/package.json: look it up on the releases page by the file date"

echo "next: compare the sha256 above with the hash GitHub lists for that exact version's sf-linux-x64 .tar.xz."
echo "      if it matches:  sudo sh /tmp/step8b.sh <that sha256>      if not, or you cannot find it: stop and tell the assistant."
