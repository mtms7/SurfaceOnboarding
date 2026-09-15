$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$opaTarget = 'workato@172.26.37.20'
$deployId = Get-Date -Format 'yyyyMMdd-HHmmss'
$localStage = Join-Path ([System.IO.Path]::GetTempPath()) "surface-phase2-$deployId"
$remoteUpload = "/home/workato/phase2-upload-$deployId"

$files = @(
    'phase2_leonardo/__init__.py',
    'phase2_leonardo/intake.py',
    'phase2_leonardo/policy.py',
    'phase2_leonardo/server_intake.py',
    'phase2_leonardo/test_intake.py',
    'phase2_leonardo/test_policy.py',
    'phase2_leonardo/contracts/case3-intake-request.schema.json',
    'phase2_leonardo/contracts/case3-preflight-response.schema.json',
    'phase2_leonardo/contracts/create-request.schema.json',
    'phase2_leonardo/contracts/create-result.schema.json',
    'phase2_leonardo/data/README.md',
    'phase2_leonardo/data/public_suffix_list.dat',
    'phase2_leonardo/fixtures/synthetic-blocked-intake-status.json'
)

try {
    New-Item -ItemType Directory -Path $localStage | Out-Null
    foreach ($relativePath in $files) {
        if (-not (Test-Path -LiteralPath $relativePath -PathType Leaf)) {
            throw "Required deployment file is missing: $relativePath"
        }
        $destination = Join-Path $localStage $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $relativePath -Destination $destination
    }

    $checksums = foreach ($relativePath in $files) {
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $localStage $relativePath)).Hash.ToLowerInvariant()
        $unixPath = $relativePath.Replace('\', '/')
        "$hash  $unixPath"
    }
    # sha256sum treats carriage returns as filename characters. Write the
    # manifest with explicit Unix LF endings even when packaging on Windows.
    $checksumText = [string]::Join("`n", $checksums) + "`n"
    [System.IO.File]::WriteAllText(
        (Join-Path $localStage 'SHA256SUMS'),
        $checksumText,
        [System.Text.Encoding]::ASCII
    )

    Write-Host "Uploading guarded Phase 2 package as $deployId"
    Write-Host 'Enter the OPA VM password only in the SSH/SCP prompt. It is not saved.'
    scp -o ConnectTimeout=15 -o NumberOfPasswordPrompts=1 -r $localStage "${opaTarget}:$remoteUpload"
    if ($LASTEXITCODE -ne 0) { throw "SCP upload failed with exit code $LASTEXITCODE" }

    $remoteScript = @'
set -eu
umask 077
deploy_id='__DEPLOY_ID__'
upload='/home/workato/phase2-upload-__DEPLOY_ID__'
operation='/home/workato/phase2-operations/__DEPLOY_ID__'
live='/home/workato/phase2_leonardo'
backup="$operation/backup/phase2_leonardo"
failed="$operation/failed/phase2_leonardo"
python_bin='/home/workato/phase1-venv/bin/python'
pid_file='/home/workato/phase2-intake.pid'
log_file='/home/workato/phase2-intake.log'

test -d "$upload/phase2_leonardo"
test -x "$python_bin"
mkdir -p "$operation/backup" "$operation/failed" "$operation/evidence"
chmod 700 "$operation" "$operation/backup" "$operation/failed" "$operation/evidence"
cd "$upload"
sha256sum -c SHA256SUMS
PYTHONPATH="/home/workato:$upload" "$python_bin" -m unittest -v phase2_leonardo.test_intake phase2_leonardo.test_policy

old_pid=''
if test -f "$pid_file"; then
  candidate=$(tr -cd '0-9' < "$pid_file")
  if test -n "$candidate" && test -r "/proc/$candidate/cmdline" && tr '\0' ' ' < "/proc/$candidate/cmdline" | grep -q 'phase2_leonardo.server_intake'; then
    old_pid="$candidate"
  fi
fi
unmanaged_pids=$(pgrep -f 'phase2_leonardo.server_intake' || true)
if test -n "$unmanaged_pids"; then
  if test -z "$old_pid" || test "$(printf '%s\n' "$unmanaged_pids" | wc -l)" -ne 1 || test "$unmanaged_pids" != "$old_pid"; then
    echo 'unmanaged or duplicate Phase 2 intake process detected' >&2
    exit 1
  fi
fi

rollback() {
  if test -n "$old_pid" && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" || true
  fi
  current_pid=''
  if test -f "$pid_file"; then current_pid=$(tr -cd '0-9' < "$pid_file"); fi
  if test -n "$current_pid" && kill -0 "$current_pid" 2>/dev/null; then kill "$current_pid" || true; fi
  if test -d "$live"; then mv "$live" "$failed" || true; fi
  if test -d "$backup"; then mv "$backup" "$live"; fi
  if test -d "$live"; then
    cd /home/workato
    nohup env PHASE2_INTAKE_PORT=8789 PYTHONPATH=/home/workato "$python_bin" -m phase2_leonardo.server_intake > "$log_file" 2>&1 &
    printf '%s\n' "$!" > "$pid_file"
    chmod 600 "$pid_file" "$log_file"
  fi
}
trap rollback HUP INT TERM ERR

if test -n "$old_pid"; then
  kill "$old_pid"
  for ignored in 1 2 3 4 5 6 7 8 9 10; do
    if ! kill -0 "$old_pid" 2>/dev/null; then break; fi
    sleep 1
  done
  if kill -0 "$old_pid" 2>/dev/null; then exit 1; fi
fi

if test -d "$live"; then mv "$live" "$backup"; fi
mv "$upload/phase2_leonardo" "$live"
cd /home/workato
PYTHONPATH=/home/workato "$python_bin" -m unittest -v phase2_leonardo.test_intake phase2_leonardo.test_policy
: > "$log_file"
chmod 600 "$log_file"
nohup env PHASE2_INTAKE_PORT=8789 PYTHONPATH=/home/workato "$python_bin" -m phase2_leonardo.server_intake > "$log_file" 2>&1 &
printf '%s\n' "$!" > "$pid_file"
chmod 600 "$pid_file"
sleep 2
curl -fsS http://127.0.0.1:8789/healthz
printf '\n'
ss -ltn | grep -F '127.0.0.1:8789'
systemctl is-active workato-agent.service
sha256sum "$live/intake.py" "$live/policy.py" "$live/server_intake.py" "$live/data/public_suffix_list.dat" > "$operation/evidence/deployed-sha256.txt"
chmod 600 "$operation/evidence/deployed-sha256.txt"
trap - HUP INT TERM ERR
printf 'deployment_id=%s\n' "$deploy_id"
'@
    $remoteScript = $remoteScript.Replace('__DEPLOY_ID__', $deployId)
    $remoteScript | ssh -o ConnectTimeout=15 -o NumberOfPasswordPrompts=1 $opaTarget 'bash -s'
    if ($LASTEXITCODE -ne 0) { throw "Remote activation failed with exit code $LASTEXITCODE" }
    Write-Host "Phase 2 intake deployment completed: $deployId"
}
finally {
    if (Test-Path -LiteralPath $localStage) {
        Remove-Item -LiteralPath $localStage -Recurse -Force
    }
}
