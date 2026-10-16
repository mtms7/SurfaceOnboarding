<#
.SYNOPSIS
Registers (or removes) the Windows scheduled task that keeps the VM dashboard tunnel open.

.DESCRIPTION
The task runs tools\keep_vm_tunnel.ps1 hidden at every sign-in of the current user, restarts it if it ever stops,
and has no time limit. It stores no secret (the key file path is only read by ssh). Needs the key from
tools\setup_vm_tunnel_key.ps1 and the VM step _step15c to be done first; a `-Test` check proves the key works
before anything is registered.
    powershell -ExecutionPolicy Bypass -File tools\register_vm_tunnel_task.ps1 -WhatIf   # preview
    powershell -ExecutionPolicy Bypass -File tools\register_vm_tunnel_task.ps1           # register and start
    powershell -ExecutionPolicy Bypass -File tools\register_vm_tunnel_task.ps1 -Remove   # unregister and stop
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$VmHost = '172.26.37.20',
    [string]$User = 'workato',
    [string]$KeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\vm_tunnel_ed25519'),
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'SurfaceOnboarding-VmTunnel'
$script = Join-Path $PSScriptRoot 'keep_vm_tunnel.ps1'

if ($Remove) {
    if ($PSCmdlet.ShouldProcess($taskName, 'Unregister scheduled task')) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Output "Removed scheduled task $taskName."
    }
    return
}

if (-not (Test-Path -LiteralPath $script)) { throw "Not found: $script" }
if (-not (Test-Path -LiteralPath $KeyFile)) { throw "No tunnel key. Run tools\setup_vm_tunnel_key.ps1 first." }

# Prove the key works (forwarding-only key: a test that returns status 0 or the forced-command refusal both mean it was accepted).
$out = & ssh.exe -i $KeyFile -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 "$User@$VmHost" 'true' 2>&1
if ($out -match 'Permission denied|Host key verification failed|Connection (timed out|refused)|No route') {
    throw "The key was not accepted ($($out | Select-Object -First 1)). Run VM step 15C first, connect the VPN, or accept the host key once with a normal ssh login. No task was registered."
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory (Split-Path -Parent $PSScriptRoot) `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $script)
# At sign-in AND every 5 minutes: if the loop ever dies (closed console, sleep/resume, logoff of a helper), the next
# repetition starts it again; while it is still running, MultipleInstances IgnoreNew skips the duplicate start.
$logon = New-ScheduledTaskTrigger -AtLogOn -User ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME)
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger = @($logon, $repeat)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($taskName, 'Register scheduled task: keep the VM dashboard tunnel open at sign-in')) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Keeps the SSH tunnel https://localhost:8443 -> VM dashboard open (tunnel-only key, no secrets in this task).' -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    Write-Output "Registered and started $taskName. Browse https://localhost:8443/ in about 15 seconds. Log: $env:LOCALAPPDATA\SurfaceOnboarding\vm-tunnel.log"
}
