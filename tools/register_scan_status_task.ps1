<#
.SYNOPSIS
Registers (or removes) the Windows scheduled task that runs the read-only Leonardo scan-status sweep.

.DESCRIPTION
The task runs `tools\attended_ce_only_playwright.py --scan-status-all` as the current user, only while that user is
signed in. The sweep is read-only: it never fills, submits, creates, or edits anything and writes no Salesforce data;
it stores each onboarded Surface / Case 3 CO's scan status and executions in integration\attended_scan_status.json.

It needs the operator's existing Leonardo Development browser session (the dedicated automation Chrome profile, signed
in once with SSO/MFA). It never handles a password, MFA code, or token. If that session is not signed in, the run opens
a tab in the automation browser and waits up to 15 minutes (MAX_WAIT_SECONDS) for an attended sign-in, then stops with
`development_login_timeout` (or `leonardo_session_expired` when the API rejects the session) and records nothing new
for the remaining COs. So an unattended run with no session fails closed, but it can leave a sign-in tab waiting.
Overlapping runs are skipped and a run is limited to 30 minutes.

Run once, in your own PowerShell:
    powershell -File tools\register_scan_status_task.ps1 -WhatIf     # preview only
    powershell -File tools\register_scan_status_task.ps1             # register
    powershell -File tools\register_scan_status_task.ps1 -Remove     # unregister

The runner prints one JSON line per run (reason codes only); Task Scheduler shows the last run result.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonPath = 'C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [ValidateRange(1, 24)][int]$IntervalHours = 4,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$taskName = 'SurfaceOnboarding-ScanStatus'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $projectRoot 'tools\attended_ce_only_playwright.py'

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
if (-not (Test-Path -LiteralPath $runner)) { throw "Runner not found: $runner" }

$action = New-ScheduledTaskAction -Execute $PythonPath -Argument ('"{0}" --scan-status-all' -f $runner) -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) -LogonType Interactive -RunLevel Limited

if ($PSCmdlet.ShouldProcess($taskName, "Register scheduled task: $PythonPath runner --scan-status-all every $IntervalHours h")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Read-only Leonardo Development scan-status sweep for Surface Onboarding (needs the signed-in automation browser; no secrets in this task).' -Force | Out-Null
    Write-Output "Registered $taskName (every $IntervalHours hours, while $env:USERNAME is signed in)."
}
