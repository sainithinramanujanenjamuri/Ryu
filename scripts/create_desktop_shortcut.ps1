# Creates a Windows Desktop shortcut for RYU AI Command Center
$WshShell = New-Object -ComObject WScript.Shell
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TargetPath = Join-Path $RepoRoot "launch_ryu.bat"
$IconPath = Join-Path $RepoRoot "apps\ryu-desktop\src-tauri\icons\icon.ico"

$desktopLocations = @(
    [System.Environment]::GetFolderPath("Desktop"),
    [System.Environment]::GetFolderPath("CommonDesktopDirectory")
) | Select-Object -Unique

foreach ($dest in $desktopLocations) {
    if (-not [string]::IsNullOrWhiteSpace($dest) -and (Test-Path $dest)) {
        try {
            $shortcutPath = Join-Path $dest "RYU AI Command Center.lnk"
            $shortcut = $WshShell.CreateShortcut($shortcutPath)
            $shortcut.TargetPath = $TargetPath
            $shortcut.WorkingDirectory = $RepoRoot
            $shortcut.Description = "Launch RYU AI Command Center"
            if (Test-Path $IconPath) {
                $shortcut.IconLocation = "$IconPath,0"
            }
            $shortcut.Save()
            Write-Host "Created Desktop shortcut: $shortcutPath" -ForegroundColor Green
        } catch {
            Write-Host "Could not create shortcut in $dest : $_" -ForegroundColor Yellow
        }
    }
}

# Refresh Windows Explorer shell notification
try {
    $code = @'
    [System.Runtime.InteropServices.DllImport("shell32.dll")]
    public static extern void SHChangeNotify(int wEventId, int uFlags, int dwItem1, int dwItem2);
'@
    $type = Add-Type -MemberDefinition $code -Name ShellUtil -Namespace Win32 -PassThru -ErrorAction SilentlyContinue
    if ($type) {
        $type::SHChangeNotify(0x08000000, 0x0000, 0, 0) # SHCNE_ASSOCCHANGED
    }
} catch {}

