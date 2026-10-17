<#
.SYNOPSIS
Registers (or removes) the Windows scheduled task that pushes the production clone to the VM every hour (interim path, docs/51).

.DESCRIPTION
The task runs tools\run_clone_push.ps1 hidden as the current user, only while that user is signed in (the Redash key is a DPAPI blob
that only this Windows user can open) and the VPN is up; it stores no secret. Run it ONLY after: VM steps 22A and 22B, the push key
from tools\setup_clone_push_key.ps1, a working `redash_inventory_collector.py --probe`, and your own go. A `-Test` style check proves the
key is accepted before anything is registered (the VM answers refused:empty_input to an empty bundle; nothing changes there).
    powershell -ExecutionPolicy Bypass -File tools\register_clone_push_task.ps1 -WhatIf   # preview
    powershell -ExecutionPolicy Bypass -File tools\register_clone_push_task.ps1           # register and start
    powershell -ExecutionPolicy Bypass -File tools\register_clone_push_task.ps1 -Remove   # unregister and stop
The older task SurfaceOnboarding-RedashInventory becomes redundant (this task collects too); it can stay.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonPath = 'C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [string]$VmHost = '172.26.37.20',
    [string]$User = 'workato',
    [string]$PushKey = (Join-Path $env:USERPROFILE '.surface-onboarding\clone_push_ed25519'),
    [string]$HmacKeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\snapshot.key'),
    [ValidateRange(15, 180)][int]$IntervalMinutes = 60,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'SurfaceOnboarding-CloneToVm'
$script = Join-Path $PSScriptRoot 'run_clone_push.ps1'

if ($Remove) {
    if ($PSCmdlet.ShouldProcess($taskName, 'Unregister scheduled task')) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Output "Removed scheduled task $taskName. (VM side: step 22B revert, then step 22A revert.)"
    }
    return
}

foreach ($path in @($script, $PythonPath, $PushKey, $HmacKeyFile)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Not found: $path (run tools\setup_clone_push_key.ps1 / supply -PythonPath / -HmacKeyFile first)." }
}

# Prove the push key is accepted: an empty body must be answered by the forced command with a refusal code, never a shell.
# (Skipped under -WhatIf: a preview must not contact the VM.)
if (-not $WhatIfPreference) {
$empty = Join-Path $env:TEMP 'surface-clone-push-empty.bin'
$out = Join-Path $env:TEMP 'surface-clone-push-test.out'
$err = Join-Path $env:TEMP 'surface-clone-push-test.err'
Set-Content -LiteralPath $empty -Value '' -NoNewline
try {
    $p = Start-Process -FilePath 'ssh.exe' -NoNewWindow -PassThru -Wait -RedirectStandardInput $empty -RedirectStandardOutput $out -RedirectStandardError $err `
        -ArgumentList @('-T', '-i', ('"{0}"' -f $PushKey), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=15', ('{0}@{1}' -f $User, $VmHost), 'clone-push')
    $answer = if (Test-Path -LiteralPath $out) { (Get-Content -LiteralPath $out -TotalCount 1) } else { '' }
    $problem = if (Test-Path -LiteralPath $err) { (Get-Content -LiteralPath $err -Raw) } else { '' }
} finally {
    Remove-Item -LiteralPath $empty, $out, $err -Force -ErrorAction SilentlyContinue
}
if ($answer -notmatch '^refused:(empty_input|not_approved)$') {
    $why = if ($problem -match 'Permission denied') { 'key not accepted (VM step 22B, or the from= address is wrong)' }
           elseif ($problem -match 'Host key verification failed') { 'host key not trusted yet (accept it once with a normal ssh login)' }
           elseif ($problem -match 'timed out|refused|No route') { 'VM not reachable (connect the VPN)' }
           else { 'unexpected answer' }
    throw "The push key test failed: $why. No task was registered."
}
if ($answer -eq 'refused:not_approved') { Write-Warning 'The VM answered not_approved: the approval marker is missing (VM step 22B). The task will fail until it exists.' }
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory (Split-Path -Parent $PSScriptRoot) `
    -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -PythonPath "{1}" -VmHost {2} -User {3} -PushKey "{4}" -HmacKeyFile "{5}"' -f $script, $PythonPath, $VmHost, $User, $PushKey, $HmacKeyFile)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RunOnlyIfNetworkAvailable
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($taskName, "Register scheduled task: collect and push the production clone to $VmHost every $IntervalMinutes min")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Interim: collects the Redash production clone and pushes it to the VM over a forced-command key (no secrets in this task).' -Force | Out-Null
    Write-Output "Registered $taskName (every $IntervalMinutes minutes while $env:USERNAME is signed in and the VPN is up). First run in about 2 minutes."
    Write-Output "Log: $env:LOCALAPPDATA\SurfaceOnboarding\clone-push\clone-push.log (result codes only). Check the VM with step 22C."
}
