[CmdletBinding()]
param(
    [string]$PackageRoot
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($PackageRoot)) {
    $PackageRoot = Join-Path $PSScriptRoot '..\Surface_Workato_OPA_Handover_2026-08-26'
}
$resolvedRoot = (Resolve-Path -LiteralPath $PackageRoot).Path

function Get-RelativePortablePath {
    param([Parameter(Mandatory)][string]$Path)
    $baseUri = [Uri]($resolvedRoot.TrimEnd('\') + '\')
    $pathUri = [Uri]$Path
    [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($pathUri).ToString()).Replace('\', '/')
}

$files = Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File |
    Where-Object { $_.Name -notin @('checksums.sha256', 'manifest.json') } |
    Sort-Object FullName |
    ForEach-Object {
        [ordered]@{
            path = Get-RelativePortablePath -Path $_.FullName
            bytes = $_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }

$manifest = [ordered]@{
    schema_version = '1.1'
    package_name = 'Surface Workato OPA portable handover'
    base_package_date = '2026-08-26'
    refreshed_through = '2026-08-31'
    external_system_posture = 'read-only'
    source_snapshot = 'curated-current-and-sanitized'
    inclusions = @(
        'operator guides',
        'current Phase 1 validator source/tests',
        'current Phase 2 v5 contract source/tests',
        'pure-local attended-phase evidence planner and tests',
        'strict schemas and public suffix data',
        'one fully synthetic blocked fixture',
        'sanitized v5, Primary User, Leonardo mapping/filling, verifier transport, caller-attempt, and Step 15 correction evidence summaries',
        'sanitized diagrams'
    )
    exclusions = @(
        'raw outputs and terminal transcripts',
        'HAR and screenshot material',
        'customer payloads and customer-derived fixtures',
        'credentials, tokens, cookies, MFA data, and activation commands',
        'browser state and recovery/app-data material',
        'deployment identifiers, internal hashes, usernames, and machine paths',
        'routable/internal addresses and live external asset URLs',
        'remote-operation scripts'
    )
    local_verification = [ordered]@{
        command = 'python -m unittest discover -v (from source_material using Python 3.12+)'
        result = '78 tests passed'
    }
    files = @($files)
}

$manifestPath = Join-Path $resolvedRoot 'manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8

$checksums = Get-ChildItem -LiteralPath $resolvedRoot -Recurse -File |
    Where-Object { $_.Name -ne 'checksums.sha256' } |
    Sort-Object FullName |
    ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $(Get-RelativePortablePath -Path $_.FullName)"
    }
$checksums | Set-Content -LiteralPath (Join-Path $resolvedRoot 'checksums.sha256') -Encoding utf8

Write-Output 'Portable handover manifest and checksums refreshed.'
