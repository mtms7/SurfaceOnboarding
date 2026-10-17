#!/bin/sh
# REVIEW ARTIFACT - Step 22A (operator-run on the VM as root): sudo sh /tmp/step22a.sh            (install)
#                                                              sudo sh /tmp/step22a.sh revert     (remove)
# Interim clone push (docs/51): installs the two root-owned programs and the sudoers rule that let ONE restricted SSH key hand a
# signed clone-only bundle to the ingest program, and nothing else:
#   /usr/local/sbin/surface-clone-push-gate    sshd forced command (runs as workato): refuses unless the approval marker exists, then
#                                              runs the ingest as surface-onboarding through the argument-free sudoers rule below.
#   /usr/local/sbin/surface-clone-push-ingest  runs as surface-onboarding: takes a lock, runs tools/ingest_clone_push.py with a
#                                              clean environment, a 5 minute limit and the bundle on stdin.
#   /etc/sudoers.d/surface-clone-push          workato may run exactly that one program as surface-onboarding, with no arguments.
# Needs release r34 (it contains tools/ingest_clone_push.py) to be the ACTIVE app first. Does not touch sshd, authorized_keys, any
# service or timer, the snapshots, or the network. The approval marker is created by step 22B (that IS the owner's go).
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
GATE=/usr/local/sbin/surface-clone-push-gate
INGEST=/usr/local/sbin/surface-clone-push-ingest
SUDOERS=/etc/sudoers.d/surface-clone-push
OPT=/opt/surface-onboarding
PY=$OPT/runtime/python-3.12.14/bin/python3.12
KEY=/etc/surface-onboarding/snapshot.key

if [ "${1:-}" = revert ]; then
  rm -f "$GATE" "$INGEST" "$SUDOERS"
  echo "removed gate, ingest program and sudoers rule (authorized_keys line and marker: step 22B revert)"; exit 0
fi

echo "== 1. checks =="
[ -f "$OPT/app/tools/ingest_clone_push.py" ] || { echo "tools/ingest_clone_push.py is not in the active app: activate r34 first"; exit 1; }
[ -x "$PY" ] && [ -f "$KEY" ] || { echo "missing python or snapshot key: stop here"; exit 1; }
id surface-onboarding >/dev/null && id workato >/dev/null || { echo "users missing: stop here"; exit 1; }
command -v flock >/dev/null && command -v sudo >/dev/null && command -v visudo >/dev/null && command -v timeout >/dev/null || { echo "flock, sudo, visudo or timeout missing: stop here"; exit 1; }

echo "== 2. the ingest program (runs as surface-onboarding) =="
cat > "$INGEST.new" <<'EOF_INGEST'
#!/bin/sh
# Installed by step 22A. Runs as surface-onboarding via sudo. The bundle is read from stdin by the Python ingest.
set -eu
umask 077
OPT=/opt/surface-onboarding
STATE=/var/lib/surface-onboarding
# The approval is enforced HERE, where the privilege changes: the sudoers rule is useless without the owner's marker.
[ -f /etc/surface-onboarding/approved-clone-push ] || { echo "refused:not_approved"; exit 3; }
exec 9>"$STATE/clone-push.lock"
flock -w 120 9 || { echo "refused:busy"; exit 75; }
cd "$OPT/app"
timeout 300 env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 SURFACE_ONBOARDING_STATE_DIR="$STATE" \
  "$OPT/runtime/python-3.12.14/bin/python3.12" tools/ingest_clone_push.py --key-file /etc/surface-onboarding/snapshot.key
EOF_INGEST
install -m 0755 -o root -g root "$INGEST.new" "$INGEST"; rm -f "$INGEST.new"

echo "== 3. the forced-command gate (runs as workato; reads nothing from the client except the bundle on stdin) =="
cat > "$GATE.new" <<'EOF_GATE'
#!/bin/sh
# Installed by step 22A. sshd runs this for the clone-push key whatever command the client asked for.
set -eu
[ -f /etc/surface-onboarding/approved-clone-push ] || { echo "refused:not_approved"; exit 3; }
exec sudo -n -u surface-onboarding /usr/local/sbin/surface-clone-push-ingest
EOF_GATE
install -m 0755 -o root -g root "$GATE.new" "$GATE"; rm -f "$GATE.new"

echo "== 4. sudoers rule (validated before it is installed) =="
printf '%s\n' 'workato ALL=(surface-onboarding) NOPASSWD: /usr/local/sbin/surface-clone-push-ingest ""' > "$SUDOERS.new"
visudo -cf "$SUDOERS.new" || { rm -f "$SUDOERS.new"; echo "sudoers rule invalid: nothing installed"; exit 1; }
install -m 0440 -o root -g root "$SUDOERS.new" "$SUDOERS"; rm -f "$SUDOERS.new"

echo "== 5. result =="
ls -l "$GATE" "$INGEST" "$SUDOERS" | awk '{print $1, $3, $4, $9}'
echo "done. Next: step 22B (adds the key and creates the approval marker). Revert: sudo sh /tmp/step22a.sh revert"
