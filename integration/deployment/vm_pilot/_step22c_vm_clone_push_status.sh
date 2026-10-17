#!/bin/sh
# REVIEW ARTIFACT - Step 22C (operator-run on the VM): sudo sh /tmp/step22c.sh [fallback]
# READ-ONLY status of the interim clone push: gates, the clone's age and size from latest.json, and the push key's last use.
# Prints names, counts, ages and codes only (no key, no row value). Changes nothing, contacts nothing.
#   fallback   prints (does not run) the command that makes the dashboard use the clone inside the signed snapshot again.
set -u
STATE=/var/lib/surface-onboarding
LATEST=$STATE/clone-inventory/prod-clone/latest.json
echo "== 1. gates =="
for f in /usr/local/sbin/surface-clone-push-gate /usr/local/sbin/surface-clone-push-ingest /etc/sudoers.d/surface-clone-push /etc/surface-onboarding/approved-clone-push; do
  if [ -e "$f" ]; then echo "present: $f"; else echo "missing: $f"; fi
done
AK=$(getent passwd workato | cut -d: -f6)/.ssh/authorized_keys
N=$(grep -c ' surface-clone-push$' "$AK" 2>/dev/null || true)
printf 'push key lines in authorized_keys: %s\n' "${N:-0}"
[ -f /var/lib/surface-onboarding/clone-push-high-water.json ] && echo "high-water record: present" || echo "high-water record: none (first push will apply the row floor)"

echo "== 2. clone age (latest.json) =="
if [ -f "$LATEST" ]; then
  CAP=$(sed -n 's/.*"captured_at": *"\([^"]*\)".*/\1/p' "$LATEST" | head -n 1)
  ROWS=$(sed -n 's/.*"row_count": *\([0-9]*\).*/\1/p' "$LATEST" | head -n 1)
  echo "captured_at: ${CAP:-unknown}   tenants: ${ROWS:-unknown}   file mtime: $(stat -c %y "$LATEST" | cut -c1-19)"
  THEN=0
  [ -n "$CAP" ] && THEN=$(date -u -d "$CAP" +%s 2>/dev/null || echo 0)
  if [ "$THEN" -le 0 ]; then echo "clone age: unknown -> NOT trusted"; else
    AGE=$(( ($(date -u +%s) - THEN) / 60 ))
    if [ "$AGE" -le 360 ]; then V="trusted (6 h or less)"; else V="NOT trusted (older than 6 h: production checks fail closed)"; fi
    echo "clone age: $AGE min -> $V"
  fi
else
  echo "no $LATEST yet (no push has been ingested)"
fi
echo "files: $(ls "$STATE/clone-inventory/prod-clone" 2>/dev/null | wc -l)"

echo "== 3. last pushes (sshd log: time and source only) =="
journalctl -u ssh -u sshd --no-pager -n 400 2>/dev/null | grep -i 'Accepted publickey for workato' | tail -n 5 | cut -c1-160 || true

if [ "${1:-}" = fallback ]; then
  echo "== fallback (printed, not run) =="
  echo "sudo mv $STATE/clone-inventory/prod-clone $STATE/clone-inventory/prod-clone.disabled-DATE"
  echo "then the dashboard reads the clone inside the current signed snapshot (manual step 12A) at its next request."
fi
echo "done"
