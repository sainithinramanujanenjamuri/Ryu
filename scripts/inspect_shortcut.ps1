$sh = New-Object -ComObject WScript.Shell
$sc = $sh.CreateShortcut("C:\Users\Sai Nithin\OneDrive\Desktop\RYU AI Command Center.lnk")
Write-Host "TargetPath: $($sc.TargetPath)"
Write-Host "WorkingDirectory: $($sc.WorkingDirectory)"
Write-Host "IconLocation: $($sc.IconLocation)"
