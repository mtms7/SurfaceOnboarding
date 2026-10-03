<#
.SYNOPSIS
Starts the desktop-only attended Salesforce onboarding dashboard.

.DESCRIPTION
This script is deliberately for a Windows operator workstation.  It keeps the
dashboard on 127.0.0.1, uses the operator's interactive Salesforce CLI login,
and never copies a session, cookie, or credential to the Ubuntu OPA host.

Use -Restart only when you explicitly want to stop the loopback process that
currently owns this dashboard port.

Each start prints a one-time unlock code for this dashboard only (not a
Salesforce or Leonardo password); the dashboard receives only its SHA-256.
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

function New-UnlockCode {
    # 8 characters from 32 unambiguous symbols; 256 is a multiple of 32, so no bias.
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    $bytes = New-Object byte[] 8
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $chars = foreach ($b in $bytes) { $alphabet[$b % 32] }
    $code = -join $chars
    return $code.Substring(0, 4) + '-' + $code.Substring(4, 4)
}

function Get-UnlockDigest {
    param([Parameter(Mandatory)] [string]$Code)
    $normalized = $Code.Trim().ToUpperInvariant().Replace('-', '')
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalized)) } finally { $sha.Dispose() }
    return -join ($hash | ForEach-Object { $_.ToString('x2') })
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
$unlockCode = New-UnlockCode
# The child inherits these; only the digest of the unlock code is passed.
$env:SURFACE_ONBOARDING_UNLOCK_SHA256 = Get-UnlockDigest -Code $unlockCode
$env:SURFACE_SF_TARGET_ORG = $TargetOrg
$env:SURFACE_SF_EXPECTED_ORG_ID = $ExpectedOrgId
try {
    $process = Start-Process -FilePath $PythonPath -ArgumentList $quotedDashboard -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardError $startupLog -PassThru
} finally {
    Remove-Item Env:SURFACE_ONBOARDING_UNLOCK_SHA256 -ErrorAction SilentlyContinue
}
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
# Liveness: /unlock makes no Salesforce read and needs no unlock, so it answers immediately.
try {
    $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 -Uri "http://127.0.0.1:$Port/unlock"
} catch {
    throw 'Dashboard started but did not answer its local health read. Check the desktop firewall and the listener PID above.'
}

Write-Host ''
Write-Host "Unlock code (this dashboard only, valid until it restarts): $unlockCode"
Write-Host "Next: open http://127.0.0.1:$Port/unlock, enter the code, then select Prepare sessions."
if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:$Port/unlock"
}
