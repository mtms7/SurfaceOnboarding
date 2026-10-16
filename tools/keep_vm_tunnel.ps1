<#
.SYNOPSIS
Keeps the SSH tunnel to the VM dashboard open all the time (reconnects by itself, no password).

.DESCRIPTION
Runs `ssh -N -L 8443:127.0.0.1:8443` with the dedicated tunnel-only key, and starts it again whenever it ends
(sleep, VPN or Wi-Fi drop, VM reboot). Waits 5 s, then up to 60 s between attempts while the VM is unreachable.
It never prompts: BatchMode refuses password questions, and the host key must already be known.
Log: one line per (re)connect in %LOCALAPPDATA%\SurfaceOnboarding\vm-tunnel.log (times and exit codes only).

Normally started by the scheduled task from tools\register_vm_tunnel_task.ps1; to run it by hand:
    powershell -ExecutionPolicy Bypass -File tools\keep_vm_tunnel.ps1
Then browse https://localhost:8443/ (accept the self-signed certificate). Stop with Ctrl+C or by ending the task.
#>
[CmdletBinding()]
param(
    [string]$VmHost = '172.26.37.20',
    [string]$User = 'workato',
    [int]$LocalPort = 8443,
    [string]$KeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\vm_tunnel_ed25519')
)

$ErrorActionPreference = 'Stop'
$logDir = Join-Path $env:LOCALAPPDATA 'SurfaceOnboarding'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'vm-tunnel.log'

function Write-TunnelLog([string]$Message) {
    $line = '{0:u} {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $log -Value $line
    if ((Get-Item -LiteralPath $log).Length -gt 512KB) {
        Move-Item -LiteralPath $log -Destination ($log + '.1') -Force
    }
}

if (-not (Test-Path -LiteralPath $KeyFile)) {
    Write-TunnelLog "no tunnel key at $KeyFile - run tools\setup_vm_tunnel_key.ps1 first"
    throw "Tunnel key not found: $KeyFile. Run tools\setup_vm_tunnel_key.ps1 first."
}

$delay = 5
while ($true) {
    $listening = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        # Something (this loop's ssh, or a manual tunnel) already serves the port: do not fight it.
        Start-Sleep -Seconds 30
        continue
    }
    Write-TunnelLog "connecting to $User@${VmHost}: local $LocalPort -> VM 127.0.0.1:8443"
    $started = Get-Date
    & ssh.exe -N -i $KeyFile `
        -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
        -o ExitOnForwardFailure=yes -o ConnectTimeout=15 `
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
        -L "${LocalPort}:127.0.0.1:8443" "$User@$VmHost" 2>$null
    $code = $LASTEXITCODE
    $ranFor = [int]((Get-Date) - $started).TotalSeconds
    Write-TunnelLog "tunnel ended (exit $code) after ${ranFor}s"
    # A tunnel that lived a while reconnects quickly; one that fails at once backs off up to 60 s.
    if ($ranFor -gt 120) { $delay = 5 } else { $delay = [Math]::Min($delay * 2, 60) }
    Start-Sleep -Seconds $delay
}
