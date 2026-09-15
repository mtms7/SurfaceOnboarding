<#
.SYNOPSIS
Creates a timestamped, non-destructive recovery backup before repairing or
reinstalling the ChatGPT/Codex desktop client.

.DESCRIPTION
Backs up the active Surface project collection (including Git metadata), the
current user's .codex folder, likely ChatGPT/Codex app-data locations, and a
small diagnostic snapshot. It never deletes, moves, or alters source data.

Cloud-synchronised ChatGPT/Codex chats are normally recovered by signing back
into the same account. This script preserves local caches and session state as
best effort; it cannot guarantee an export of server-side chat history.

.EXAMPLE
.\Backup-CodexChatGPT-Recovery.ps1

.EXAMPLE
.\Backup-CodexChatGPT-Recovery.ps1 -BackupRoot 'E:\RecoveryBackups'
#>
[CmdletBinding()]
param(
    [string]$BackupRoot = (Join-Path $env:USERPROFILE 'Documents\Codex-ChatGPT-Recovery-Backups'),
    [string]$ProjectRoot = (Join-Path $env:USERPROFILE 'Documents\Surface')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-FullPath {
    param([Parameter(Mandatory)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Add-Status {
    param([Parameter(Mandatory)][string]$Text)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    "$stamp  $Text" | Tee-Object -FilePath $script:StatusLog -Append
}

function Copy-RecoveryTree {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Source
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        Add-Status "SKIPPED [$Name]: not present: $Source"
        return [ordered]@{ name = $Name; source = $Source; outcome = 'skipped_not_present'; robocopy_exit_code = $null }
    }

    $destination = Join-Path $script:SourcesRoot $Name
    $robocopyLog = Join-Path $script:LogsRoot ("robocopy_{0}.log" -f $Name)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Add-Status "Copying [$Name] from $Source"

    # /XJ prevents a junction from causing recursive copies. Robocopy exit codes
    # 0-7 mean copied/no-change/warnings; 8+ means a copy failure occurred.
    & robocopy $Source $destination /E /COPY:DAT /DCOPY:T /R:2 /W:2 /XJ /ZB /NP /NFL /NDL "/LOG:$robocopyLog"
    $exitCode = $LASTEXITCODE
    $outcome = if ($exitCode -lt 8) { 'copied' } else { 'copy_failed_or_incomplete' }
    Add-Status "[$Name] finished: robocopy exit code $exitCode ($outcome)"
    return [ordered]@{ name = $Name; source = $Source; destination = $destination; outcome = $outcome; robocopy_exit_code = $exitCode }
}

$backupRootFull = Get-FullPath $BackupRoot
$projectRootFull = Get-FullPath $ProjectRoot
if ($backupRootFull.StartsWith($projectRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "BackupRoot must not be inside ProjectRoot. Choose a folder outside '$projectRootFull' to avoid recursive copies."
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupFolder = Join-Path $backupRootFull ("Codex-ChatGPT-Recovery_{0}" -f $timestamp)
$script:SourcesRoot = Join-Path $backupFolder 'sources'
$script:LogsRoot = Join-Path $backupFolder 'logs'
$metadataRoot = Join-Path $backupFolder 'metadata'
New-Item -ItemType Directory -Path $script:SourcesRoot, $script:LogsRoot, $metadataRoot -Force | Out-Null
$script:StatusLog = Join-Path $script:LogsRoot 'backup-status.log'

Add-Status "Starting non-destructive recovery backup: $backupFolder"

$diagnostics = [ordered]@{
    created_at = (Get-Date).ToString('o')
    computer_name = $env:COMPUTERNAME
    user_name = $env:USERNAME
    powershell_version = $PSVersionTable.PSVersion.ToString()
    project_root = $projectRootFull
    backup_folder = $backupFolder
}
try {
    $diagnostics.operating_system = Get-CimInstance Win32_OperatingSystem |
        Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime
} catch {
    $diagnostics.operating_system_error = $_.Exception.Message
}
try {
    $diagnostics.openai_packages = Get-AppxPackage | Where-Object { $_.Name -match 'OpenAI|ChatGPT|Codex' } |
        Select-Object Name, PackageFullName, Version, InstallLocation, Status
} catch {
    $diagnostics.openai_packages_error = $_.Exception.Message
}
$diagnostics | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $metadataRoot 'environment.json') -Encoding UTF8

try {
    Get-WinEvent -LogName Application -MaxEvents 500 -ErrorAction Stop |
        Where-Object { $_.ProviderName -match 'OpenAI|ChatGPT|Codex' -or $_.Message -match 'CryptUnprotectData|Codex|ChatGPT' } |
        Select-Object TimeCreated, Id, LevelDisplayName, ProviderName, Message |
        Format-List | Out-File -LiteralPath (Join-Path $metadataRoot 'relevant-application-events.txt') -Encoding UTF8
} catch {
    $_ | Out-String | Set-Content -LiteralPath (Join-Path $metadataRoot 'relevant-application-events-error.txt') -Encoding UTF8
}

$sources = @(
    [ordered]@{ name = 'surface-projects'; path = $projectRootFull },
    [ordered]@{ name = 'codex-home'; path = (Join-Path $env:USERPROFILE '.codex') },
    [ordered]@{ name = 'openai-roaming-appdata'; path = (Join-Path $env:APPDATA 'OpenAI') },
    [ordered]@{ name = 'chatgpt-roaming-appdata'; path = (Join-Path $env:APPDATA 'ChatGPT') },
    [ordered]@{ name = 'openai-local-appdata'; path = (Join-Path $env:LOCALAPPDATA 'OpenAI') },
    [ordered]@{ name = 'chatgpt-local-appdata'; path = (Join-Path $env:LOCALAPPDATA 'ChatGPT') }
)

$packagesRoot = Join-Path $env:LOCALAPPDATA 'Packages'
if (Test-Path -LiteralPath $packagesRoot -PathType Container) {
    Get-ChildItem -LiteralPath $packagesRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'OpenAI|ChatGPT|Codex' } |
        ForEach-Object {
            $sources += [ordered]@{ name = ('package-' + $_.Name); path = $_.FullName }
        }
}

$results = foreach ($source in $sources) {
    Copy-RecoveryTree -Name $source.name -Source $source.path
}

Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $metadataRoot 'Backup-CodexChatGPT-Recovery.ps1') -Force
[ordered]@{
    created_at = (Get-Date).ToString('o')
    backup_folder = $backupFolder
    project_root = $projectRootFull
    results = @($results)
    note = 'Robocopy exit codes 0-7 are successful copies or warnings. Inspect logs for every code 8 or higher before reinstalling.'
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $metadataRoot 'backup-manifest.json') -Encoding UTF8

Add-Status 'Backup complete. Review metadata\backup-manifest.json and logs\backup-status.log before reinstalling.'
Write-Host "`nBackup created at: $backupFolder" -ForegroundColor Green
Write-Host 'Before reinstalling, confirm every robocopy exit code in metadata\backup-manifest.json is below 8.' -ForegroundColor Yellow
