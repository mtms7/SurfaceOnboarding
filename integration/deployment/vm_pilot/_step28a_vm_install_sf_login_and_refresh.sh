#!/bin/sh
# REVIEW ARTIFACT - Step 28A (operator-run on the VM as root): sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh install <MY_DOMAIN_URL>
#                                                              sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh status
#                                                              sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh revert
# Owner decision 2026-10-27: the VM is the final automation host and refreshes its own Salesforce data from an attended sign-in
# (the same SSO and MFA the desktop uses, done in the operator's own browser). This installs, and nothing else:
#   /usr/local/sbin/surface-sf-login    root-owned program: starts the Salesforce CLI web login with the session folder in memory
#                                       (/run/surface-onboarding-sf), prints the one-time URL for the operator's own browser, waits for
#                                       the sign-in over the SSH port forward (-L 1717), checks it with one status-only query, and
#                                       starts one refresh at once. It never sees a password, MFA code or token.
#   /usr/local/sbin/surface-sf-logout   root-owned program: revokes the CLI token at Salesforce and empties the memory folder.
#   /etc/tmpfiles.d/surface-onboarding-sf.conf   recreates the private memory folder after every reboot.
#   /etc/surface-onboarding/sf-login-url         the My Domain URL (not secret), written once by this script.
#   /etc/surface-onboarding/approved-sf-reader   the approval marker the refresh unit requires (this script IS the owner's go).
#   surface-onboarding-sf-refresh.service/.timer from the release templates (read-only fixed queries, every 10 minutes, only works
#                                       while someone has signed in; otherwise the page says "Salesforce session needed").
# After a reboot the session is gone by design (memory only): sign in again with surface-sf-login. Needs a release that contains
# tools/vm_refresh_snapshot.py (r26 or later) as the ACTIVE app and the Salesforce CLI already installed (step 8B).
# Touches no snapshot, no sshd setting, no firewall rule, no dashboard or web-proxy service, no push-channel file, no Salesforce write.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
MODE=${1:-}
OPT=/opt/surface-onboarding
ETC=/etc/surface-onboarding
SF=$OPT/sf-cli/bin/sf
LOGIN=/usr/local/sbin/surface-sf-login
LOGOUT=/usr/local/sbin/surface-sf-logout
TMPFILES=/etc/tmpfiles.d/surface-onboarding-sf.conf
MARKER=$ETC/approved-sf-reader
URLFILE=$ETC/sf-login-url
UNIT_DIR=/etc/systemd/system
SERVICE=surface-onboarding-sf-refresh.service
TIMER=surface-onboarding-sf-refresh.timer
TEMPLATES=$OPT/app/integration/deployment/vm_pilot

case "$MODE" in
status)
  echo "marker:     $([ -f "$MARKER" ] && echo present || echo absent)"
  echo "login url:  $([ -f "$URLFILE" ] && echo present || echo absent)"
  echo "programs:   $([ -x "$LOGIN" ] && echo login) $([ -x "$LOGOUT" ] && echo logout)"
  echo "timer:      $(systemctl is-enabled "$TIMER" 2>/dev/null || echo not-installed) / $(systemctl is-active "$TIMER" 2>/dev/null || echo inactive)"
  systemctl list-timers "$TIMER" --no-pager 2>/dev/null | sed -n '1,3p' || true
  echo "session:    $(find /run/surface-onboarding-sf -mindepth 1 -maxdepth 1 2>/dev/null | wc -l) entries in memory"
  echo "last refresh result (journal, codes only):"
  journalctl -u "$SERVICE" -n 6 --no-pager 2>/dev/null | cut -c1-160 || true
  exit 0;;
revert)
  systemctl disable --now "$TIMER" 2>/dev/null || true
  rm -f "$UNIT_DIR/$SERVICE" "$UNIT_DIR/$TIMER" "$MARKER" "$URLFILE" "$TMPFILES"
  [ -x "$LOGOUT" ] && "$LOGOUT" || true
  rm -f "$LOGIN" "$LOGOUT"
  systemctl daemon-reload
  echo "removed the timer, units, marker, login url, tmpfiles entry and both programs (the snapshots and the Salesforce CLI are untouched)"
  exit 0;;
install) ;;
*) echo "usage: sh _step28a_vm_install_sf_login_and_refresh.sh install <MY_DOMAIN_URL> | status | revert"; exit 1;;
esac

LOGIN_URL=${2:-}
printf '%s' "$LOGIN_URL" | grep -Eq '^https://[a-z0-9.-]+\.salesforce\.com$' || { echo "usage: ... install https://<mydomain>.my.salesforce.com"; exit 1; }

echo "== 1. checks =="
[ -x "$SF" ] || { echo "Salesforce CLI missing at $SF: run step 8A/8B first"; exit 1; }
[ -f "$ETC/snapshot.key" ] || { echo "snapshot key missing: stop here"; exit 1; }
[ -f "$OPT/app/tools/vm_refresh_snapshot.py" ] || { echo "the active app has no tools/vm_refresh_snapshot.py: stop here"; exit 1; }
[ -f "$TEMPLATES/surface-onboarding-sf-refresh.service.template" ] && [ -f "$TEMPLATES/surface-onboarding-sf-refresh.timer.template" ] \
  || { echo "unit templates missing in the active app: stop here"; exit 1; }
id surface-onboarding >/dev/null || { echo "service user missing: stop here"; exit 1; }
command -v systemctl >/dev/null && command -v systemd-tmpfiles >/dev/null || { echo "systemd tools missing: stop here"; exit 1; }
echo ok

echo "== 2. the login program (runs as root through sudo; the browser step is yours) =="
cat > "$LOGIN.new" <<'EOF_LOGIN'
#!/bin/sh
# Installed by step 28A. Attended Salesforce sign-in for the VM refresher. The session lives only in /run/surface-onboarding-sf.
# Run from the PC:  ssh -t -L 1717:127.0.0.1:1717 workato@172.26.37.20 "sudo /usr/local/sbin/surface-sf-login"
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
SF=/opt/surface-onboarding/sf-cli/bin/sf
SESSION=/run/surface-onboarding-sf
HOOKDIR=/opt/surface-onboarding/ops/sf-login-hook
RUNAS=surface-onboarding
ALIAS=surface-onboarding
URLFILE=$SESSION/login-url.txt
LOG=$SESSION/login.log
LOGIN_URL=$(cat /etc/surface-onboarding/sf-login-url)
printf '%s' "$LOGIN_URL" | grep -Eq '^https://[a-z0-9.-]+\.salesforce\.com$' || { echo "bad /etc/surface-onboarding/sf-login-url"; exit 1; }
install -d -m 0700 -o "$RUNAS" -g "$RUNAS" "$SESSION"
install -d -m 0755 -o root -g root "$HOOKDIR"
cat > "$HOOKDIR/open-url.sh" <<'HOOK_EOF'
#!/bin/sh
# Records the URL the CLI wants opened; opens nothing. The URL is one-time and holds no secret of ours.
printf '%s\n' "${1:-}" > /run/surface-onboarding-sf/login-url.txt
HOOK_EOF
chown root:root "$HOOKDIR/open-url.sh"
chmod 0755 "$HOOKDIR/open-url.sh"
rm -f "$URLFILE" "$LOG"
echo "== starting the login (the CLI waits on localhost:1717 for your browser; keep this SSH session open) =="
sudo -u "$RUNAS" env HOME="$SESSION" BROWSER="$HOOKDIR/open-url.sh" PATH="/usr/local/bin:/usr/bin:/bin" \
  SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true SF_AUTOUPDATE_DISABLE=true SFDX_AUTOUPDATE_DISABLE=true \
  SF_SKIP_NEW_VERSION_CHECK=true SF_WEB_OAUTH_SERVER_TIMEOUT=300000 \
  "$SF" org login web --alias "$ALIAS" --instance-url "$LOGIN_URL" --no-prompt > "$LOG" 2>&1 &
PID=$!
i=0
while [ $i -lt 20 ] && [ ! -s "$URLFILE" ] && kill -0 "$PID" 2>/dev/null; do i=$((i+1)); sleep 1; done
if [ ! -s "$URLFILE" ]; then
  echo "no sign-in URL was produced. CLI output (no token):"
  sed -n '1,8p' "$LOG" | cut -c1-200
  kill "$PID" 2>/dev/null || true
  exit 1
fi
echo "Open this URL in YOUR browser (the one with your SSO), sign in with SSO and MFA, then wait here:"
echo
cat "$URLFILE"
echo
echo "== waiting for the sign-in to complete (up to 5 min) =="
i=0
while kill -0 "$PID" 2>/dev/null && [ $i -lt 300 ]; do i=$((i+1)); sleep 1; done
if kill -0 "$PID" 2>/dev/null; then kill "$PID" 2>/dev/null || true; echo "timed out: no sign-in was completed (is the SSH forward -L 1717 open?)"; rm -f "$URLFILE"; exit 1; fi
wait "$PID" || { echo "the CLI reported a failure (no token shown):"; sed -n '1,10p' "$LOG" | cut -c1-200; rm -f "$URLFILE"; exit 1; }
rm -f "$URLFILE" "$LOG"
echo "== verify with one tiny read-only query (status only, no values) =="
sudo -u "$RUNAS" env HOME="$SESSION" SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true \
  "$SF" data query --query "SELECT Id FROM Organization LIMIT 1" --target-org "$ALIAS" --json 2>/dev/null | grep -o '"status": *[0-9]*' | head -1
echo "== starting one refresh now =="
systemctl start --no-block surface-onboarding-sf-refresh.service 2>/dev/null || echo "(refresh unit not installed)"
echo "signed in (memory only, ends at reboot). to end it now: sudo /usr/local/sbin/surface-sf-logout"
EOF_LOGIN
install -m 0755 -o root -g root "$LOGIN.new" "$LOGIN"; rm -f "$LOGIN.new"

echo "== 3. the logout program =="
cat > "$LOGOUT.new" <<'EOF_LOGOUT'
#!/bin/sh
# Installed by step 28A. Revokes the CLI session at Salesforce (best effort) and empties the memory folder. Safe with no session.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
SF=/opt/surface-onboarding/sf-cli/bin/sf
SESSION=/run/surface-onboarding-sf
RUNAS=surface-onboarding
ALIAS=surface-onboarding
[ -d "$SESSION" ] || { echo "no session folder: nothing to do"; exit 0; }
case "$SESSION" in /run/*) ;; *) echo "unexpected session path"; exit 1;; esac
if [ -x "$SF" ]; then
  sudo -u "$RUNAS" env HOME="$SESSION" SF_USE_GENERIC_UNIX_KEYCHAIN=true SF_DISABLE_TELEMETRY=true \
    "$SF" org logout --target-org "$ALIAS" --no-prompt 2>&1 | sed 's/[A-Za-z0-9_.-]*@[A-Za-z0-9_.-]*/<user>/g' | cut -c1-160 || true
fi
find "$SESSION" -mindepth 1 -delete
echo "session ended; the next refresh will say 'Salesforce session needed' until someone signs in again"
EOF_LOGOUT
install -m 0755 -o root -g root "$LOGOUT.new" "$LOGOUT"; rm -f "$LOGOUT.new"

echo "== 4. memory folder after every reboot, login url, units, marker, timer =="
printf 'd /run/surface-onboarding-sf 0700 surface-onboarding surface-onboarding -\n' > "$TMPFILES"
chmod 0644 "$TMPFILES"
systemd-tmpfiles --create "$TMPFILES"
printf '%s\n' "$LOGIN_URL" > "$URLFILE"
chmod 0644 "$URLFILE"
install -m 0644 -o root -g root "$TEMPLATES/surface-onboarding-sf-refresh.service.template" "$UNIT_DIR/$SERVICE"
install -m 0644 -o root -g root "$TEMPLATES/surface-onboarding-sf-refresh.timer.template" "$UNIT_DIR/$TIMER"
: > "$MARKER"
chmod 0644 "$MARKER"
systemctl daemon-reload
systemctl enable --now "$TIMER"

echo "== 5. state =="
ls -l "$LOGIN" "$LOGOUT" "$TMPFILES" "$MARKER" "$URLFILE"
ls -ld /run/surface-onboarding-sf
systemctl is-enabled "$TIMER"; systemctl is-active "$TIMER"
echo "installed. Next: sign in from the PC:  ssh -t -L 1717:127.0.0.1:1717 workato@172.26.37.20 \"sudo /usr/local/sbin/surface-sf-login\""
echo "undo everything: sudo sh /tmp/_step28a_vm_install_sf_login_and_refresh.sh revert"
