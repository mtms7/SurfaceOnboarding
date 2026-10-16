#!/bin/sh
# REVIEW ARTIFACT - Step 18A (operator-run on the VM as root): sudo sh /tmp/step18a.sh            (install the key)
#                                                              sudo sh /tmp/step18a.sh revert     (remove it)
# Installs the read-only Redash key of saved query 251 as /etc/surface-onboarding/redash-key, owner root, group
# surface-redash-reader, mode 0640 (the collector refuses anything looser). The key is typed at a HIDDEN prompt: it is never
# an argument, never printed, never logged, never in shell history. Only its SHAPE (20-64 letters/digits) is checked here.
# Does not contact Redash, create a marker, install a unit or start anything (that is step 18B).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
DIR=/etc/surface-onboarding
KEY=$DIR/redash-key
GROUP=surface-redash-reader

if [ "${1:-}" = revert ]; then
  rm -f "$KEY" "$KEY.new"
  echo "removed $KEY (if it existed); nothing else changed"
  exit 0
fi

echo "== 1. checks =="
[ -d "$DIR" ] || { echo "$DIR missing: stop here"; exit 1; }
getent group "$GROUP" >/dev/null || { echo "group $GROUP missing: run step 6A first"; exit 1; }
[ -t 0 ] || { echo "needs an interactive terminal (hidden prompt): stop here"; exit 1; }
if [ -e "$KEY" ]; then echo "a key file already exists: run 'revert' first to replace it: stop here"; exit 1; fi

echo "== 2. key (input is hidden; paste, then Enter) =="
printf 'Redash query-251 API key: '
stty -echo
trap 'stty echo' EXIT INT TERM
IFS= read -r VALUE || VALUE=
stty echo
trap - EXIT INT TERM
echo
VALUE=$(printf '%s' "$VALUE" | tr -d '\r\n ')
case "$VALUE" in
  ''|*[!A-Za-z0-9]*) VALUE=; echo "not the shape of a Redash key (letters and digits only): nothing written"; exit 1 ;;
esac
LEN=$(printf '%s' "$VALUE" | wc -c | tr -d ' ')
if [ "$LEN" -lt 20 ] || [ "$LEN" -gt 64 ]; then VALUE=; echo "wrong length: nothing written"; exit 1; fi

echo "== 3. write root:$GROUP 0640 =="
umask 0137
printf '%s\n' "$VALUE" > "$KEY.new"
VALUE=
chown root:"$GROUP" "$KEY.new"
chmod 0640 "$KEY.new"
mv "$KEY.new" "$KEY"
ls -l "$KEY"
echo "key length: $LEN characters (value not shown)"
echo "next: step 18B. undo: sudo sh /tmp/step18a.sh revert"
