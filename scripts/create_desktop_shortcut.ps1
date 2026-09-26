# Creates a Windows Desktop shortcut for RYU AI Command Center
$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [System.Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $DesktopPath "RYU AI Command Center.lnk"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TargetPath = Join-Path $RepoRoot "launch_ryu.bat"
$IconPath = Join-Path $RepoRoot "apps\ryu-desktop\src-tauri\icons\icon.ico"

$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $TargetPath
$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = "Launch RYU AI Command Center"
if (Test-Path $IconPath) {
    $Shortcut.IconLocation = "$IconPath,0"
}
$Shortcut.Save()

Write-Host "Created Desktop shortcut: $ShortcutPath" -ForegroundColor Green
