<#
.SYNOPSIS
One unattended run of the interim clone push (docs/51): collect the production clone from Redash's cached result, build a
clone-only signed bundle, send it to the VM over the restricted push key.

.DESCRIPTION
Run by the scheduled task SurfaceOnboarding-CloneToVm (tools\register_clone_push_task.ps1) every hour while you are signed in and on
the VPN. Needs no browser, no MFA, no Claude. Stores and prints no secret: the Redash key stays in its DPAPI blob (read by the
collector), the HMAC key and the push key are files only your Windows user can read, and the only text logged is a result code.
Steps: 1 collector --collect --cached-ok --query-id 253 (v2, with the CE scan fields; owner decision 2026-10-28)  2 build bundle (re-verifies the local clone: at most 6 h old)  4 (after the push) the Salesforce snapshot
is built by publish_vm_snapshot.py and pushed the same way (best effort, never changes the exit code; owner request 2026-10-27)  3 ssh to the VM with the
forced-command key (the bundle goes in on stdin; the VM verifies and answers one line: ingested | unchanged | refused:<code>).
Exit: 0 ingested or unchanged, 1 collector failed (the push still tried the last good local clone), 2 build refused,
3 VM refused, 4 VM not reachable (VPN down or key not accepted). Log: %LOCALAPPDATA%\SurfaceOnboarding\clone-push\clone-push.log
#>
[CmdletBinding()]
param(
    [string]$PythonPath = 'C:\Users\Milton Stevenson\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [string]$VmHost = '172.26.37.20',
    [string]$User = 'workato',
    [string]$PushKey = (Join-Path $env:USERPROFILE '.surface-onboarding\clone_push_ed25519'),
    [string]$HmacKeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\snapshot.key'),
    [ValidateRange(15, 1440)][int]$SnapshotEveryMinutes = 180,
    [switch]$PushSnapshot
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$work = Join-Path $env:LOCALAPPDATA 'SurfaceOnboarding\clone-push'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$log = Join-Path $work 'clone-push.log'
$bundle = Join-Path $work 'bundle.zip'
$stdout = Join-Path $work 'ssh.out'
$stderr = Join-Path $work 'ssh.err'

function Write-Log([string]$text) {
    if ((Test-Path -LiteralPath $log) -and ((Get-Item -LiteralPath $log).Length -gt 1MB)) { Move-Item -LiteralPath $log -Destination ($log + '.old') -Force }
    Add-Content -LiteralPath $log -Value ("{0} {1}" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'), $text)
}

# The push result is only ever one of these shapes; anything else is logged as a code, never echoed.
function Get-ResultCode([string]$file) {
    if (-not (Test-Path -LiteralPath $file)) { return 'no_output' }
    $line = (Get-Content -LiteralPath $file -TotalCount 1)
    if ($line -match '^(ingested|unchanged) captured_at=[0-9TZ:-]+ rows=[0-9]+$') { return $line }
    if ($line -match '^refused:[a-z_]{1,40}$') { return $line }
    return 'unexpected_output'
}

# Native calls on Windows PowerShell 5.1: with $ErrorActionPreference='Stop', ANY stderr text from a native command (even
# redirected with 2>$null) becomes a terminating error. So the preference is relaxed inside this function only, stderr goes to a
# file that is never read or logged, and the result is the last stdout line plus $LASTEXITCODE.
function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $errFile = Join-Path $work 'native.err'
    $lines = @(& $exe @arguments 2>$errFile)
    $code = $LASTEXITCODE
    Remove-Item -LiteralPath $errFile -Force -ErrorAction SilentlyContinue
    return [pscustomobject]@{ Out = [string]($lines | Select-Object -Last 1); Exit = $code }
}

try {
    # 1. collect (the collector writes only reason codes and counts; the Redash key is read from its DPAPI blob)
    $collected = Invoke-Native $PythonPath @((Join-Path $projectRoot 'tools\redash_inventory_collector.py'), '--collect', '--cached-ok', '--query-id', '253')
    $collectOut = $collected.Out
    $collectExit = $collected.Exit
    $collectCode = 'collector_no_output'
    try { $collectCode = [string](($collectOut | ConvertFrom-Json).result) } catch { }
    Write-Log "collect exit=$collectExit result=$collectCode"

    # 2. build the clone-only signed bundle
    if (Test-Path -LiteralPath $bundle) { Remove-Item -LiteralPath $bundle -Force }
    $built = Invoke-Native $PythonPath @((Join-Path $projectRoot 'tools\build_clone_push_bundle.py'), '--key-file', $HmacKeyFile, '--out', $bundle)
    $buildOut = $built.Out
    if ($built.Exit -ne 0 -or -not (Test-Path -LiteralPath $bundle)) {
        Write-Log "build $buildOut"
        exit 2
    }
    Write-Log "build $buildOut"

    # 3. push: forced-command key, no prompt, pinned host key, bundle on stdin (a pipe would corrupt binary in PowerShell 5.1)
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    $sshArgs = @('-T', '-i', ('"{0}"' -f $PushKey), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=8', ('{0}@{1}' -f $User, $VmHost), 'clone-push')
    $process = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -NoNewWindow -PassThru -Wait `
        -RedirectStandardInput $bundle -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $result = Get-ResultCode $stdout
    $transport = if (Test-Path -LiteralPath $stderr) { (Get-Content -LiteralPath $stderr -Raw) } else { '' }
    if ($process.ExitCode -eq 255 -or $transport -match 'Permission denied|Host key verification failed|timed out|refused|No route') {
        Write-Log "push unreachable_or_key_not_accepted ssh_exit=$($process.ExitCode)"
        exit 4
    }
    Write-Log "push ssh_exit=$($process.ExitCode) result=$result"

    # 3b. the masked production create records for the VM queue ("Verified", the onboarded line; owner request 2026-10-27). Fast (local
    # files only), best effort, never changes this run's exit code. The VM merges them into its current snapshot and answers one line.
    try {
        $recordsZip = Join-Path $work 'records-bundle.zip'
        if (Test-Path -LiteralPath $recordsZip) { Remove-Item -LiteralPath $recordsZip -Force }
        $recordsBuilt = Invoke-Native $PythonPath @((Join-Path $projectRoot 'tools\build_records_push_bundle.py'), '--key-file', $HmacKeyFile, '--out', $recordsZip)
        Write-Log "records_build exit=$($recordsBuilt.Exit)"
        if ($recordsBuilt.Exit -eq 0 -and (Test-Path -LiteralPath $recordsZip)) {
            Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
            $recordsProcess = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -NoNewWindow -PassThru -Wait `
                -RedirectStandardInput $recordsZip -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $recordsLine = if (Test-Path -LiteralPath $stdout) { [string](Get-Content -LiteralPath $stdout -TotalCount 1) } else { '' }
            $recordsResult = 'unexpected_output'
            if ($recordsLine -match '^(ingested-records snapshot-[0-9]{8}T[0-9]{6}Z(-[0-9]{1,3})?|unchanged-records)$') { $recordsResult = $recordsLine }
            elseif ($recordsLine -match '^refused:[a-z_]{1,40}$') { $recordsResult = $recordsLine }
            Write-Log "records_push ssh_exit=$($recordsProcess.ExitCode) result=$recordsResult"
        }
    } catch {
        Write-Log "records_failed $($_.Exception.GetType().Name)"
    }

    # 4. OPTIONAL (-PushSnapshot; the VM refreshes Salesforce itself since 2026-10-27): the Salesforce snapshot for the VM page. Best effort: it
    # never changes this run's exit code. Same restricted key and command; the VM accepts only the producer label below on this channel.
    # The build takes about 6.5 minutes (about 390 Salesforce CLI reads), so it runs at most every $SnapshotEveryMinutes minutes.
    $snapStamp = Join-Path $work 'snapshot-last-success.txt'
    $snapDue = [bool]$PushSnapshot
    if ($PushSnapshot -and (Test-Path -LiteralPath $snapStamp)) {
        try { $snapDue = ((Get-Date).ToUniversalTime() - [datetime]::Parse((Get-Content -LiteralPath $snapStamp -TotalCount 1), [cultureinfo]::InvariantCulture, 'AdjustToUniversal,AssumeUniversal')).TotalMinutes -ge $SnapshotEveryMinutes } catch { $snapDue = $true }
    }
    try {
        if (-not $snapDue) { throw [System.OperationCanceledException]::new('snapshot_not_due') }
        $snapDir = Join-Path $work 'snapshot-out'
        New-Item -ItemType Directory -Force -Path $snapDir | Out-Null
        $snapZip = Join-Path $snapDir 'surface-vm-snapshot-latest.zip'
        if (Test-Path -LiteralPath $snapZip) { Remove-Item -LiteralPath $snapZip -Force }
        $published = Invoke-Native $PythonPath @((Join-Path $projectRoot 'tools\publish_vm_snapshot.py'), '--key-file', $HmacKeyFile, '--out-dir', $snapDir, '--producer', 'desktop-snapshot-push', '--autopublish')
        Write-Log "snapshot_build exit=$($published.Exit)"
        if ($published.Exit -eq 0 -and (Test-Path -LiteralPath $snapZip)) {
            Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
            $snapProcess = Start-Process -FilePath 'ssh.exe' -ArgumentList $sshArgs -NoNewWindow -PassThru -Wait `
                -RedirectStandardInput $snapZip -RedirectStandardOutput $stdout -RedirectStandardError $stderr
            $snapLine = if (Test-Path -LiteralPath $stdout) { [string](Get-Content -LiteralPath $stdout -TotalCount 1) } else { '' }
            $snapResult = 'unexpected_output'
            if ($snapLine -match '^ingested-snapshot snapshot-[0-9]{8}T[0-9]{6}Z(-[0-9]{1,3})?$') { $snapResult = $snapLine }
            elseif ($snapLine -match '^refused:[a-z_]{1,40}$') { $snapResult = $snapLine }
            Write-Log "snapshot_push ssh_exit=$($snapProcess.ExitCode) result=$snapResult"
            if ($snapProcess.ExitCode -eq 0 -and $snapResult -like 'ingested-snapshot *') {
                Set-Content -LiteralPath $snapStamp -Value (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') -Encoding ascii
            }
        }
    } catch [System.OperationCanceledException] {
        # not due yet: nothing to log
    } catch {
        Write-Log "snapshot_failed $($_.Exception.GetType().Name)"
    }
    if ($process.ExitCode -eq 0 -and $result -match '^(ingested|unchanged) ') {
        if ($collectExit -ne 0 -and $collectExit -ne 2) { exit 1 }
        exit 0
    }
    exit 3
} catch {
    Write-Log "run_failed $($_.Exception.GetType().Name)"
    exit 1
} finally {
    Remove-Item -LiteralPath $bundle, $stdout, $stderr -Force -ErrorAction SilentlyContinue
    if ($snapZip) { Remove-Item -LiteralPath $snapZip -Force -ErrorAction SilentlyContinue }
    if ($recordsZip) { Remove-Item -LiteralPath $recordsZip -Force -ErrorAction SilentlyContinue }
}
