<#
.SYNOPSIS
Registers (or removes) the Windows scheduled task that runs the read-only Redash inventory collector.

.DESCRIPTION
The task runs `tools\redash_inventory_collector.py --collect` as the current user, only while that user is
signed in (the stored Redash key is a DPAPI blob that only this Windows user can open). It needs no browser,
no MFA, and no Claude. Overlapping runs are skipped and a run is limited to 10 minutes.

Run once, in your own PowerShell, after `--store-key` and a successful `--probe`:
    powershell -File tools\register_redash_inventory_task.ps1 -WhatIf     # preview only
    powershell -File tools\register_redash_inventory_task.ps1             # register
    powershell -File tools\register_redash_inventory_task.ps1 -Remove     # unregister

Results are one line per run in %LOCALAPPDATA%\SurfaceOnboarding\redash\collector.log (reason codes and counts only).
Exit codes: 0 = ok, 1 = failed (nothing written), 2 = written but Redash could not refresh (data is older than wanted);
Task Scheduler shows 1 and 2 as a failed last run.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonPath = 'C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [ValidateRange(15, 1440)][int]$IntervalMinutes = 60,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'SurfaceOnboarding-RedashInventory'
$projectRoot = Split-Path -Parent $PSScriptRoot
$collector = Join-Path $projectRoot 'tools\redash_inventory_collector.py'
$keyFile = Join-Path $env:LOCALAPPDATA 'SurfaceOnboarding\redash\query-251.key'

if ($Remove) {
    if ($PSCmdlet.ShouldProcess($taskName, 'Unregister scheduled task')) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        Write-Output "Removed scheduled task $taskName."
    }
    return
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python runtime not found: $PythonPath. Supply -PythonPath with an approved desktop Python 3.12+ runtime."
}
if (-not (Test-Path -LiteralPath $collector)) { throw "Collector not found: $collector" }
if (-not (Test-Path -LiteralPath $keyFile)) {
    throw "No Redash key is stored yet. Run: `"$PythonPath`" `"$collector`" --store-key   (then --probe)"
}

# A task that cannot work must not be registered: the probe reads the cached result and checks its shape only.
$probe = (& $PythonPath $collector --probe 2>$null | Select-Object -Last 1 | ConvertFrom-Json)
if ($null -eq $probe -or $probe.normalize -ne 'ok') {
    $why = if ($null -eq $probe) { 'no output' } else { [string]$probe.normalize }
    throw "The Redash probe did not pass ($why). Fix that first; no task was registered."
}

$action = New-ScheduledTaskAction -Execute $PythonPath -Argument ('"{0}" --collect' -f $collector) -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($taskName, "Register scheduled task: $PythonPath collector --collect every $IntervalMinutes min")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Read-only Redash production-clone tenant inventory for Surface Onboarding (no secrets in this task).' -Force | Out-Null
    Write-Output "Registered $taskName (every $IntervalMinutes minutes, while $env:USERNAME is signed in)."
}
