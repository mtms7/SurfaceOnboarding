#!/bin/sh
# REVIEW ARTIFACT - Step 16A (operator-run on the VM, in an interactive terminal so the password prompt works):
#   sudo sh /tmp/_step16a_vm_lan_users.sh add person@pentera.io     (prompts twice for the password; not echoed, never printed)
#   sudo sh /tmp/_step16a_vm_lan_users.sh remove person@pentera.io
#   sudo sh /tmp/_step16a_vm_lan_users.sh list                       (emails only, never hashes)
# Maintains /etc/nginx/surface-onboarding/htpasswd (0640 root:www-data) for the internal-network site (step 16B).
# Fails closed: the email must already be listed in /etc/surface-onboarding/users.txt (that file also sets the role);
# the password must be 14+ characters; a bcrypt (or, if unavailable, SHA-512) hash is stored, never the password.
# Changes nothing else; no nginx reload needed (nginx reads the file per request).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
F=/etc/nginx/surface-onboarding/htpasswd
USERS=/etc/surface-onboarding/users.txt
ACTION=${1:-}
EMAIL=${2:-}
install -d -o root -g root -m 0755 /etc/nginx/surface-onboarding
[ -f "$F" ] || { : > "$F"; chown root:www-data "$F"; chmod 0640 "$F"; }

case "$ACTION" in
list)
  cut -d: -f1 "$F" | sed 's/^/  /'; echo "entries: $(grep -c . "$F" || true)"; exit 0;;
remove)
  echo "$EMAIL" | grep -E -q '^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$' || { echo "usage: remove person@pentera.io"; exit 1; }
  grep -v "^$EMAIL:" "$F" > "$F.new" || true
  cat "$F.new" > "$F"; rm -f "$F.new"; chown root:www-data "$F"; chmod 0640 "$F"
  echo "removed $EMAIL; entries left: $(grep -c . "$F" || true)"; exit 0;;
add) ;;
*) echo "usage: sh _step16a_vm_lan_users.sh add|remove|list [email]"; exit 1;;
esac

echo "$EMAIL" | grep -E -q '^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$' || { echo "usage: add person@pentera.io"; exit 1; }
[ -f "$USERS" ] || { echo "$USERS missing: stop here"; exit 1; }
awk -v e="$EMAIL" '$1==e {found=1} END {exit !found}' "$USERS" || { echo "$EMAIL is not in $USERS: add it there first (the dashboard refuses unlisted emails): stop here"; exit 1; }
[ -t 0 ] || { echo "needs an interactive terminal (password prompt): use the VM session tab"; exit 1; }

python3 - "$EMAIL" "$F" <<'PY'
import crypt, getpass, os, re, shutil, sys, tempfile
email, path = sys.argv[1], sys.argv[2]
p1 = getpass.getpass("New password for %s (14+ characters, not shown): " % email)
if len(p1) < 14:
    sys.exit("too short: nothing changed")
if getpass.getpass("Repeat the password: ") != p1:
    sys.exit("the two entries differ: nothing changed")
method = crypt.METHOD_BLOWFISH if crypt.METHOD_BLOWFISH in crypt.methods else crypt.METHOD_SHA512
h = crypt.crypt(p1, crypt.mksalt(method))
if not h or h == p1 or ":" in h:
    sys.exit("could not hash: nothing changed")
rows = []
with open(path) as fh:
    rows = [l for l in fh.read().splitlines() if l and not l.startswith(email + ":")]
rows.append("%s:%s" % (email, h))
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
with os.fdopen(fd, "w") as out:
    out.write("\n".join(rows) + "\n")
shutil.chown(tmp, "root", "www-data")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
print("stored a %s hash for %s" % ("bcrypt" if method == crypt.METHOD_BLOWFISH else "SHA-512", email))
PY
echo "entries: $(grep -c . "$F")"
