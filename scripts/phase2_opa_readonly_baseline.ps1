$ErrorActionPreference = 'Stop'

Write-Host 'OPA validator read-only baseline'
Write-Host 'Enter the VM password only at the SSH prompt. It is not saved by this script.'

ssh -o ConnectTimeout=10 -o NumberOfPasswordPrompts=1 workato@172.26.37.20 @'
set -eu
hostname
id
date -u
pgrep -af phase1_validator || true
ss -ltnp
curl -fsS http://127.0.0.1:8787/healthz
printf '\n'
sha256sum /home/workato/phase1_validator/server.py /home/workato/phase1_validator/test_server.py
systemctl is-active workato-agent.service
'@

if ($LASTEXITCODE -ne 0) {
    Write-Error "SSH baseline failed with exit code $LASTEXITCODE. No VM changes were requested."
}

Write-Host ''
Read-Host 'Read-only baseline finished. Press Enter to close this window'
