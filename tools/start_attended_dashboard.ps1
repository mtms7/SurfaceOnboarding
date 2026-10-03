<#
.SYNOPSIS
Starts the desktop-only attended Salesforce onboarding dashboard.

.DESCRIPTION
This script is deliberately for a Windows operator workstation.  It keeps the
dashboard on 127.0.0.1, uses the operator's interactive Salesforce CLI login,
and never copies a session, cookie, or credential to the Ubuntu OPA host.

Use -Restart only when you explicitly want to stop the loopback process that
currently owns this dashboard port.

The dashboard login is the operator's Salesforce SSO (no dashboard
password): only the -AllowedUser address(es) may sign in, and signing in
also starts the Leonardo Development sign-in and the preflight checks.
Every Salesforce read is pinned to the -TargetOrg alias and must belong to
the org Id in -ExpectedOrgId (or the first line of
%LOCALAPPDATA%\SurfaceOnboarding\salesforce-expected-org-id.txt). The org Id
is not a secret.
#>
[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$NoLogin,
    [switch]$NoBrowser,
    [int]$Port = 8012,
    [string]$TargetOrg = 'surface-onboarding',
    [string]$ExpectedOrgId = '',
    [string]$AllowedUser = 'milton.stevenson@pentera.io',
    [string]$PasswordResetUrl = '',
    [string]$PythonPath = 'C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw 'This attended dashboard must run on a Windows operator desktop, not on the Ubuntu OPA host.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$dashboard = Join-Path $PSScriptRoot 'serve_attended_open_onboardings_dashboard.py'
if (-not (Test-Path -LiteralPath $dashboard)) {
    throw "Dashboard script not found: $dashboard"
}
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Python runtime not found: $PythonPath. Supply -PythonPath with an approved desktop Python 3.12+ runtime."
}
if (-not (Get-Command sf.cmd -ErrorAction SilentlyContinue)) {
    throw 'Salesforce CLI (sf.cmd) is not available on PATH. Install or repair the approved Salesforce CLI before continuing.'
}

function Invoke-SalesforceCli {
    param(
        [Parameter(Mandatory)] [string[]]$Arguments,
        [switch]$Quiet
    )

    # Salesforce CLI can write its non-fatal update notice to stderr. Do not
    # let that notice trip this script's strict error policy; trust its exit
    # code instead. Restore the caller's policy immediately afterwards.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Quiet) {
            & sf.cmd @Arguments *> $null
        } else {
            & sf.cmd @Arguments
        }
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Test-SalesforceCliSession {
    # Output is discarded (org display includes an access token).
    return (Invoke-SalesforceCli -Arguments @('org', 'display', '--target-org', $TargetOrg, '--json') -Quiet) -eq 0
}

if ($TargetOrg -notmatch '^[A-Za-z0-9_.-]{1,64}$') {
    throw 'TargetOrg must be a plain Salesforce CLI alias.'
}
if (-not $ExpectedOrgId) {
    $orgIdFile = Join-Path $env:LOCALAPPDATA 'SurfaceOnboarding\salesforce-expected-org-id.txt'
    if (Test-Path -LiteralPath $orgIdFile) {
        $ExpectedOrgId = (Get-Content -LiteralPath $orgIdFile -TotalCount 1).Trim()
    }
}
foreach ($user in ($AllowedUser -split ',')) {
    if ($user.Trim() -notmatch '^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$') {
        throw 'AllowedUser must be one or more comma-separated email addresses.'
    }
}
if ($PasswordResetUrl -and $PasswordResetUrl -notmatch '^https://') {
    throw 'PasswordResetUrl must be an https URL.'
}
if ($ExpectedOrgId -and $ExpectedOrgId -notmatch '^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$') {
    throw 'ExpectedOrgId must be a 15- or 18-character Salesforce Id.'
}
if (-not $ExpectedOrgId) {
    Write-Warning 'No expected Salesforce org Id is configured. The dashboard will show "org not pinned" and refuse Start/Validate until -ExpectedOrgId (or the org-id file) is set.'
}

if (-not (Test-SalesforceCliSession)) {
    if ($NoLogin) {
        Write-Host 'No usable Salesforce CLI session is available. The dashboard will start and show its attended Salesforce sign-in action.'
    } else {
        Write-Host 'No usable Salesforce CLI session is available. The dashboard will start and offer the attended browser SSO/MFA sign-in action.'
    }
}

$listeners = @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
    if (-not $Restart) {
        $pids = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ', '
        throw "Port $Port is already in use by PID(s) $pids. Verify the page first, or rerun with -Restart to stop only these loopback listener(s)."
    }
    foreach ($listener in $listeners) {
        Stop-Process -Id $listener.OwningProcess -Confirm:$false -ErrorAction Stop
    }
    Start-Sleep -Milliseconds 500
}

$startupLog = Join-Path $env:TEMP 'surface-onboarding-attended-dashboard-startup.log'
Remove-Item -LiteralPath $startupLog -Force -ErrorAction SilentlyContinue
# Start-Process joins ArgumentList before creating the child command line.
# Quote the script path explicitly because the operator profile path contains
# a space (for example, "Milton Stevenson").
$quotedDashboard = '"' + $dashboard + '"'
# The child inherits these non-secret settings.
$env:SURFACE_SF_TARGET_ORG = $TargetOrg
$env:SURFACE_SF_EXPECTED_ORG_ID = $ExpectedOrgId
$env:SURFACE_DASHBOARD_ALLOWED_USERS = $AllowedUser
$env:SURFACE_PASSWORD_RESET_URL = $PasswordResetUrl
$process = Start-Process -FilePath $PythonPath -ArgumentList $quotedDashboard -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardError $startupLog -PassThru
$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 250
    $listener = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
} until ($listener -or (Get-Date) -ge $deadline)

if (-not $listener) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Confirm:$false -ErrorAction SilentlyContinue }
    $startupDetail = if (Test-Path -LiteralPath $startupLog) { (Get-Content -LiteralPath $startupLog -Raw).Trim() } else { '' }
    if ($startupDetail.Length -gt 1200) { $startupDetail = $startupDetail.Substring(0, 1200) + '…' }
    if ([string]::IsNullOrWhiteSpace($startupDetail)) { $startupDetail = 'No Python startup output was produced.' }
    throw "Dashboard did not bind to 127.0.0.1:$Port. No service was left running. Startup detail: $startupDetail"
}

Write-Host "Dashboard is ready at http://127.0.0.1:$Port/ (listener PID $($listener.OwningProcess))."
# Liveness: /login makes no Salesforce read and needs no sign-in, so it answers immediately.
try {
    $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri "http://127.0.0.1:$Port/login"
} catch {
    throw 'Dashboard started but did not answer its local health read. Check the desktop firewall and the listener PID above.'
}

Write-Host ''
if (-not $NoBrowser) {
    # The dashboard opens as a tab of the automation Chrome (its own profile, not the
    # work profile), so the Leonardo tabs the runs drive open in the same window.
    $runner = Join-Path $PSScriptRoot 'attended_ce_only_playwright.py'
    $opened = $null
    # A Python warning on stderr must not trip this script's strict error policy.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $opened = (& $PythonPath $runner --open-dashboard $Port 2>$null | Select-Object -Last 1 | ConvertFrom-Json).result
    } catch {
        $opened = $null
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($opened -in @('dashboard_tab_opened', 'dashboard_tab_activated')) {
        Write-Host 'The dashboard is open in the automation Chrome window; Leonardo tabs open next to it.'
    } else {
        Write-Warning "The automation window could not be opened ($opened). Opening the dashboard in the default browser instead."
        Start-Process "http://127.0.0.1:$Port/login"
    }
}
Write-Host "Next: select Sign in with Salesforce (OneLogin SSO and MFA) at http://127.0.0.1:$Port/login."
Write-Host 'Signing in also starts the Leonardo Development sign-in in the automation window. Connect the RND VPN first.'
