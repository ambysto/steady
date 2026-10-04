# Adds/removes a shortcut in the current user's Startup folder that launches
# ping-logger.ps1 hidden at logon. No admin needed.
#   .\install-autostart.ps1            install
#   .\install-autostart.ps1 -Remove    uninstall
param([switch]$Remove)
$ErrorActionPreference = 'Stop'

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'StableInternet ping-logger.lnk'
if ($Remove) {
    Remove-Item $lnk -ErrorAction SilentlyContinue
    Write-Host "Removed: $lnk"
    return
}

$vbs = Join-Path $PSScriptRoot 'start-logger-hidden.vbs'
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath       = Join-Path $env:WINDIR 'System32\wscript.exe'
$s.Arguments        = '"' + $vbs + '"'
$s.WorkingDirectory = $PSScriptRoot
$s.Description      = 'StableInternet: background ping logger'
$s.Save()
Write-Host "Installed: $lnk -> wscript `"$vbs`""
