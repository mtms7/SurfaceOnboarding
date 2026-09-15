<#
.SYNOPSIS
Starts the desktop-only attended Salesforce onboarding dashboard.

.DESCRIPTION
This script is deliberately for a Windows operator workstation.  It keeps the
dashboard on 127.0.0.1, uses the operator's interactive Salesforce CLI login,
and never copies a session, cookie, or credential to the Ubuntu OPA host.

Use -Restart only when you explicitly want to stop the loopback process that
currently owns this dashboard port.
#>
[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$NoLogin,
    [int]$Port = 8012,
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
    return (Invoke-SalesforceCli -Arguments @('org', 'display', '--json') -Quiet) -eq 0
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
try {
    $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 40 -Uri "http://127.0.0.1:$Port/"
    $statusCode = [int]$response.StatusCode
} catch {
    if ($_.Exception.Response) {
        $statusCode = [int]$_.Exception.Response.StatusCode
    } else {
        throw 'Dashboard started but did not answer its local health read. Check the desktop firewall and the listener PID above.'
    }
}

if ($statusCode -eq 503) {
    Write-Host 'Dashboard is ready but Salesforce sign-in is required. Open the dashboard and select Sign in to Salesforce, complete browser SSO/MFA, then refresh.'
    exit 0
}
if ($statusCode -ne 200) {
    throw "Dashboard listener is running, but its local health read returned HTTP $statusCode. Inspect the loopback page before restarting."
}

Write-Host 'Salesforce queue read succeeded. Open http://127.0.0.1:8012/ in this desktop browser.'
