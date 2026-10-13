<#
Opens the SSH tunnel to the read-only VM pilot dashboard and keeps it open in this window.
Run:  powershell -File tools\open_vm_tunnel.ps1        then browse https://localhost:8443/  (accept the self-signed certificate).
Close the tunnel with Ctrl+C. Nothing is stored; SSH asks for the workato password in this window (never pass it as an argument).
Needs the pilot running on the VM (it starts at boot after the step 5A approval; otherwise sudo sh /opt/surface-onboarding/ops/pilot_start_stop.sh start).
#>
param([string]$VmHost = "172.26.37.20", [string]$User = "workato", [int]$LocalPort = 8443)
if (Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Local port $LocalPort is already in use (an earlier tunnel?). Close it, or pass -LocalPort 9443 and browse to that port."
    exit 1
}
Write-Host "Tunnel https://localhost:$LocalPort/ -> ${VmHost}:8443 (loopback on the VM). Ctrl+C to close."
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L "${LocalPort}:127.0.0.1:8443" "$User@$VmHost"
