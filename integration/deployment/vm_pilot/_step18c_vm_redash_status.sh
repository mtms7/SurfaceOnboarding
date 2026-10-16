#!/bin/sh
# REVIEW ARTIFACT - Step 18C (operator-run on the VM): sudo sh /tmp/step18c.sh
# READ-ONLY status of the Redash production-clone collector: timer state, last run result, clone age from latest.json,
# last collector.log line. Changes nothing, contacts nothing, prints no key and no row value (counts, codes, times only).
set -u
UNIT=surface-onboarding-redash-collect
STATE=/var/lib/surface-onboarding
LATEST=$STATE/clone-inventory/prod-clone/latest.json
LOG=$STATE/redash/collector.log

echo "== 1. gates =="
if [ -f /etc/surface-onboarding/approved-redash-collector ]; then echo "approval marker: present"; else echo "approval marker: missing"; fi
if [ -f /etc/surface-onboarding/redash-key ]; then echo "key file: present ($(stat -c '%U:%G %a' /etc/surface-onboarding/redash-key))"; else echo "key file: missing"; fi

echo "== 2. timer and last run =="
printf 'timer active=%s enabled=%s\n' "$(systemctl is-active $UNIT.timer 2>&1)" "$(systemctl is-enabled $UNIT.timer 2>&1)"
systemctl list-timers $UNIT.timer --no-pager 2>/dev/null | head -n 3
systemctl show $UNIT.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp 2>&1

echo "== 3. clone age (latest.json) =="
if [ -f "$LATEST" ]; then
  CAP=$(sed -n 's/.*"captured_at": *"\([^"]*\)".*/\1/p' "$LATEST" | head -n 1)
  ROWS=$(sed -n 's/.*"row_count": *\([0-9]*\).*/\1/p' "$LATEST" | head -n 1)
  echo "captured_at: ${CAP:-unknown}   tenants: ${ROWS:-unknown}"
  if [ -n "$CAP" ]; then
    THEN=$(date -u -d "$CAP" +%s 2>/dev/null || echo 0)
    NOW=$(date -u +%s)
    if [ "$THEN" -gt 0 ]; then
      AGE=$(( (NOW - THEN) / 60 ))
      if [ "$AGE" -le 360 ]; then VERDICT="trusted (6 h or less)"; else VERDICT="NOT trusted (older than 6 h)"; fi
      echo "clone age: $AGE min -> $VERDICT"
    fi
  fi
else
  echo "no $LATEST yet (the collector has not produced a clone)"
fi

echo "== 4. last collector log line (codes and counts only) =="
if [ -f "$LOG" ]; then tail -n 1 "$LOG" | cut -c1-400; else echo "no log yet"; fi
echo "meaning: refreshed:false + refresh_reason:redash_refresh_not_permitted = the latest cached result was read; it is fresh only while the Redash owner's hourly schedule runs."
