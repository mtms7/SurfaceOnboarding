param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9]{8}-[0-9]{6}$')]
    [string]$DeploymentId
)

$ErrorActionPreference = 'Stop'
Write-Host "Rolling back Phase 2 intake deployment $DeploymentId"
Write-Host 'Enter the VM password only at the SSH prompt. It is not saved.'

$remoteScript = @'
set -eu
deploy_id='__DEPLOY_ID__'
operation="/home/workato/phase2-operations/$deploy_id"
backup="$operation/backup/phase2_leonardo"
live='/home/workato/phase2_leonardo'
failed="$operation/rollback-replaced/phase2_leonardo"
python_bin='/home/workato/phase1-venv/bin/python'
pid_file='/home/workato/phase2-intake.pid'
log_file='/home/workato/phase2-intake.log'

test -d "$backup"
mkdir -p "$operation/rollback-replaced"
chmod 700 "$operation/rollback-replaced"
if test -f "$pid_file"; then
  pid=$(tr -cd '0-9' < "$pid_file")
  if test -n "$pid" && test -r "/proc/$pid/cmdline" && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'phase2_leonardo.server_intake'; then
    kill "$pid"
  fi
fi
if test -d "$live"; then mv "$live" "$failed"; fi
mv "$backup" "$live"
cd /home/workato
PYTHONPATH=/home/workato "$python_bin" -m unittest -v phase2_leonardo.test_intake phase2_leonardo.test_policy
: > "$log_file"
chmod 600 "$log_file"
nohup env PHASE2_INTAKE_PORT=8789 PYTHONPATH=/home/workato "$python_bin" -m phase2_leonardo.server_intake > "$log_file" 2>&1 &
printf '%s\n' "$!" > "$pid_file"
chmod 600 "$pid_file"
sleep 2
curl -fsS http://127.0.0.1:8789/healthz
printf '\nrollback_complete=%s\n' "$deploy_id"
'@
$remoteScript = $remoteScript.Replace('__DEPLOY_ID__', $DeploymentId)
$remoteScript | ssh -o ConnectTimeout=15 -o NumberOfPasswordPrompts=1 workato@172.26.37.20 'bash -s'
if ($LASTEXITCODE -ne 0) { throw "Rollback failed with exit code $LASTEXITCODE" }

