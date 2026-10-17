<#
.SYNOPSIS
Creates the dedicated, push-only SSH key for the interim clone push (run once, in your own PowerShell).

.DESCRIPTION
Generates an ed25519 key pair at %USERPROFILE%\.surface-onboarding\clone_push_ed25519 (no passphrase, so the hourly task can run
unattended; only your Windows user can read the folder). The PRIVATE key stays on this PC and is never shown. The VM side
(_step22b_vm_authorize_clone_push_key.sh) pins the PUBLIC half to one forced command (the clone-only ingest), no pty, no
forwarding, and your VPN source address: the key cannot open a shell, run another command or forward a port. It is a different key
from the tunnel key. Prints the one-line public key file path to upload.
    powershell -ExecutionPolicy Bypass -File tools\setup_clone_push_key.ps1
Remove everything later: delete the two key files and run VM step 22B with `revert`.
#>
[CmdletBinding()]
param([string]$KeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\clone_push_ed25519'))

$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $KeyFile
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (Test-Path -LiteralPath $KeyFile) {
    Write-Output "A clone-push key already exists: $KeyFile (kept; nothing overwritten)."
} else {
    # An empty passphrase must be '""' on Windows PowerShell 5.1 (legacy quoting) but '' on PowerShell 7.3+ (standard quoting).
    $emptyPassphrase = if ($PSVersionTable.PSVersion -ge [version]'7.3') { '' } else { '""' }
    & ssh-keygen.exe -q -t ed25519 -N $emptyPassphrase -C 'surface-clone-push' -f $KeyFile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $KeyFile)) { throw 'ssh-keygen failed; no key was created.' }
    # Only this Windows user may read the private key.
    & icacls.exe $KeyFile /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
}
$pub = $KeyFile + '.pub'
if (-not (Test-Path -LiteralPath $pub)) { throw "Public key file missing: $pub" }
Write-Output "Public key (safe to upload): $pub"
Write-Output "Fingerprint: $((& ssh-keygen.exe -l -f $pub) -join ' ')"
