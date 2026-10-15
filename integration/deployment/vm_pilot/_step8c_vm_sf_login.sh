#!/bin/sh
# REVIEW ARTIFACT - Step 8C (operator-run ATTENDED on the VM as root: sudo sh /tmp/step8c.sh <LOGIN_URL>). Spike for docs/43 section 8.
# Signs the Salesforce CLI in on the VM with YOUR own SSO and MFA, done in YOUR browser. The VM never sees a password or code.
# The session lives only in /run/surface-onboarding-sf (memory). Needs an SSH session opened with a port forward so the browser's
# redirect reaches the VM:   ssh -t -L 1717:127.0.0.1:1717 workato@172.26.37.20 "sudo sh /tmp/step8c.sh <LOGIN_URL>"
# Web login is NOT officially supported on headless hosts: this is a spike. The CLI wants to open a browser; here it is given an
# opener that only RECORDS the URL (attempt 1: the BROWSER hook; attempt 2: `--browser chrome` with a stub named google-chrome). The
# opener scripts live in /opt/surface-onboarding/ops/sf-login-hook (root-owned) because /run is mounted noexec. They contain no secret.
# If neither attempt gets a URL the script prints the CLI's own message (no token) and stops.
# <LOGIN_URL> is your My Domain URL, e.g. https://<mydomain>.my.salesforce.com (not secret). Alias used by the dashboard: surface-onboarding.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
LOGIN_URL=${1:-}
printf '%s' "$LOGIN_URL" | grep -Eq '^https://[a-z0-9.-]+\.salesforce\.com$' || { echo "usage: sh step8c.sh https://<mydomain>.my.salesforce.com"; exit 1; }
SF=/opt/surface-onboarding/sf-cli/bin/sf
SESSION=/run/surface-onboarding-sf
HOOKDIR=/opt/surface-onboarding/ops/sf-login-hook
RUNAS=surface-onboarding
ALIAS=surface-onboarding
URLFILE=$SESSION/login-url.txt
LOG=$SESSION/login.log
[ -x "$SF" ] && [ -d "$SESSION" ] || { echo "run step 8B first"; exit 1; }

echo "== 0. opener scripts (root-owned, executable, outside the noexec /run) =="
mount | grep -E ' on /run ' | sed 's/.*(/run mount options: (/' || true
install -d -m 0755 -o root -g root "$HOOKDIR"
cat > "$HOOKDIR/open-url.sh" <<'HOOK_EOF'
#!/bin/sh
# Records the URL the CLI wants opened; opens nothing. The URL is one-time and holds no secret of ours.
printf '%s\n' "${1:-}" > /run/surface-onboarding-sf/login-url.txt
HOOK_EOF
for name in google-chrome google-chrome-stable chromium chromium-browser; do
  cp "$HOOKDIR/open-url.sh" "$HOOKDIR/$name"
done
chown root:root "$HOOKDIR"/*
chmod 0755 "$HOOKDIR"/*
ls -l "$HOOKDIR"

run_login() {
  # $1 = attempt label, remaining args = extra CLI flags
  label=$1; shift
  rm -f "$URLFILE" "$LOG"
  echo "== $label: starting the login in the background (the CLI waits on localhost:1717 for your browser) =="
  sudo -u "$RUNAS" env HOME="$SESSION" BROWSER="$HOOKDIR/open-url.sh" PATH="$HOOKDIR:/usr/local/bin:/usr/bin:/bin" \
    SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true SF_AUTOUPDATE_DISABLE=true SFDX_AUTOUPDATE_DISABLE=true \
    SF_SKIP_NEW_VERSION_CHECK=true SF_WEB_OAUTH_SERVER_TIMEOUT=300000 \
    "$SF" org login web --alias "$ALIAS" --instance-url "$LOGIN_URL" --no-prompt "$@" > "$LOG" 2>&1 &
  PID=$!
  i=0
  while [ $i -lt 20 ] && [ ! -s "$URLFILE" ] && kill -0 "$PID" 2>/dev/null; do i=$((i+1)); sleep 1; done
}

run_login "attempt 1 (BROWSER hook)"
if [ ! -s "$URLFILE" ]; then
  echo "attempt 1 captured no URL. CLI output (no token):"
  sed -n '1,8p' "$LOG" | cut -c1-200
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
  run_login "attempt 2 (--browser chrome with the stub)" --browser chrome
fi

if [ -s "$URLFILE" ]; then
  echo "Open this URL in YOUR browser (the one with your SSO), sign in with SSO and MFA, then wait here:"
  echo
  cat "$URLFILE"
  echo
else
  echo "attempt 2 captured no URL either. CLI output (no token):"
  sed -n '1,12p' "$LOG" | cut -c1-200
  kill "$PID" 2>/dev/null || true
  echo "web login could not start on this host: report this to the assistant (fallbacks: custom connected app, JWT, desktop publisher)."
  exit 1
fi

echo "== waiting for the sign-in to complete (up to 5 min) =="
i=0
while kill -0 "$PID" 2>/dev/null && [ $i -lt 300 ]; do i=$((i+1)); sleep 1; done
if kill -0 "$PID" 2>/dev/null; then kill "$PID" 2>/dev/null || true; echo "timed out: no sign-in was completed (is the SSH forward -L 1717 open?)"; rm -f "$URLFILE"; exit 1; fi
wait "$PID" || { echo "the CLI reported a failure (no token shown):"; sed -n '1,10p' "$LOG" | cut -c1-200; rm -f "$URLFILE"; exit 1; }
rm -f "$URLFILE"
sed -n '1,4p' "$LOG" | sed 's/[A-Za-z0-9_.-]*@[A-Za-z0-9_.-]*/<user>/g' | cut -c1-160

echo "== verify with one tiny read-only query (status only, no values) =="
sudo -u "$RUNAS" env HOME="$SESSION" SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true \
  "$SF" data query --query "SELECT Id FROM Organization LIMIT 1" --target-org "$ALIAS" --json 2>/dev/null | grep -o '"status": *[0-9]*' | head -1

echo "== where the session lives (names, owners, modes only) =="
ls -ld "$SESSION"; df -hT "$SESSION" | sed -n '2p' | awk '{print "filesystem type: "$2}'
find "$SESSION" -maxdepth 2 -printf '%M %u %p\n' | sed -n '1,14p'
echo "signed in (memory only). to end the session now: sudo sh /tmp/step8d.sh"
