# Codex/ChatGPT recovery backup

Run the backup from a normal, non-administrator PowerShell window before reinstalling the desktop client:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\tools\Backup-CodexChatGPT-Recovery.ps1
```

To place the backup on an external drive instead:

```powershell
.\tools\Backup-CodexChatGPT-Recovery.ps1 -BackupRoot 'E:\RecoveryBackups'
```

The default destination is `%USERPROFILE%\Documents\Codex-ChatGPT-Recovery-Backups`. It copies the complete `Documents\Surface` project collection (including `.git`), `%USERPROFILE%\.codex`, likely Codex/ChatGPT app-data locations, and diagnostic metadata.

Review `metadata\backup-manifest.json` and `logs\backup-status.log` in the newly created backup. Any Robocopy exit code of `8` or higher means that source was incomplete; resolve that before reinstalling.

Do not restore the entire backup blindly after IT repairs the client. Restore projects first; they are safe to copy back. Sign back into ChatGPT/Codex to recover cloud-synchronised chats. Only restore an app-data subfolder if IT confirms it is safe, because restoring the old protected-data blob could reproduce the `CryptUnprotectData` failure.
