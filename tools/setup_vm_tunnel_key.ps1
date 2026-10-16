<#
.SYNOPSIS
Creates the dedicated, tunnel-only SSH key for the VM dashboard tunnel (run once, in your own PowerShell).

.DESCRIPTION
Generates an ed25519 key pair at %USERPROFILE%\.surface-onboarding\vm_tunnel_ed25519 (no passphrase, so the
tunnel can reconnect unattended; only your Windows user can read the folder). The PRIVATE key stays on this PC and is
never shown. The VM side (_step15c_vm_authorize_tunnel_key.sh) restricts the PUBLIC half to forwarding 127.0.0.1:8443
only: no shell, no command, no other port. Prints the one-line public key file path to upload.
    powershell -ExecutionPolicy Bypass -File tools\setup_vm_tunnel_key.ps1
Remove everything later: delete the two key files and run the VM script with `revert`.
#>
[CmdletBinding()]
param([string]$KeyFile = (Join-Path $env:USERPROFILE '.surface-onboarding\vm_tunnel_ed25519'))

$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $KeyFile
New-Item -ItemType Directory -Force -Path $dir | Out-Null
if (Test-Path -LiteralPath $KeyFile) {
    Write-Output "A tunnel key already exists: $KeyFile (kept; nothing overwritten)."
} else {
    # '""' passes an empty passphrase through Windows PowerShell 5.1 quoting.
    & ssh-keygen.exe -q -t ed25519 -N '""' -C 'surface-vm-tunnel' -f $KeyFile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $KeyFile)) { throw 'ssh-keygen failed; no key was created.' }
    # Only this Windows user may read the private key.
    & icacls.exe $KeyFile /inheritance:r /grant:r "$($env:USERNAME):F" | Out-Null
}
$pub = $KeyFile + '.pub'
if (-not (Test-Path -LiteralPath $pub)) { throw "Public key file missing: $pub" }
Write-Output "Public key (safe to upload): $pub"
Write-Output "Fingerprint: $((& ssh-keygen.exe -l -f $pub) -join ' ')"
