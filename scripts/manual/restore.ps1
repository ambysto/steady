# Reverts scripts/manual/apply-lowrisk.ps1 using a backup JSON it produced.
# Usage (elevated):  .\restore.ps1                 -> uses the newest data\manual\backup-*.json
#                    .\restore.ps1 -Backup <path>
param(
    [string]$Backup
)
$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outDir = Join-Path $root 'data\manual'
if (-not $Backup) {
    $Backup = (Get-ChildItem $outDir -Filter 'backup-*.json' | Sort-Object Name -Descending | Select-Object -First 1).FullName
}
if (-not $Backup -or -not (Test-Path $Backup)) { throw "No backup file found." }
Start-Transcript -Path (Join-Path $outDir ("restore-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))) | Out-Null
Write-Host "Restoring from $Backup"
$b = Get-Content $Backup -Raw | ConvertFrom-Json
$Adapter = $b.adapter

$SUB_WIRELESS = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'; $SET_WIRELESS = '12bbebe6-58d6-4636-95bb-3217ef867c1a'
$SUB_PCIE     = '501a4d13-42af-4429-9fd1-a8218c268e20'; $SET_ASPM     = 'ee12f906-d277-404b-b6da-e5fa1a576df5'

foreach ($p in $b.advanced.PSObject.Properties) {
    if ($p.Value) {
        Write-Host "Set '$($p.Name)' -> $($p.Value)"
        Set-NetAdapterAdvancedProperty -Name $Adapter -DisplayName $p.Name -DisplayValue $p.Value -NoRestart
    }
}

if ($null -eq $b.pnpCapabilities) {
    Write-Host "Remove PnPCapabilities (was absent)"
    Remove-ItemProperty -Path $b.classKey -Name PnPCapabilities -ErrorAction SilentlyContinue
} else {
    Write-Host "Set PnPCapabilities -> $($b.pnpCapabilities)"
    Set-ItemProperty -Path $b.classKey -Name PnPCapabilities -Value ([int]$b.pnpCapabilities) -Type DWord
}

powercfg /setacvalueindex SCHEME_CURRENT $SUB_WIRELESS $SET_WIRELESS $b.powerWireless.AC
powercfg /setdcvalueindex SCHEME_CURRENT $SUB_WIRELESS $SET_WIRELESS $b.powerWireless.DC
powercfg /setacvalueindex SCHEME_CURRENT $SUB_PCIE $SET_ASPM $b.powerAspm.AC
powercfg /setdcvalueindex SCHEME_CURRENT $SUB_PCIE $SET_ASPM $b.powerAspm.DC
powercfg /setactive SCHEME_CURRENT
Write-Host "Power settings restored (wireless AC/DC=$($b.powerWireless.AC)/$($b.powerWireless.DC), ASPM AC/DC=$($b.powerAspm.AC)/$($b.powerAspm.DC))"

Write-Host "Restarting adapter '$Adapter'..."
Restart-NetAdapter -Name $Adapter
Write-Host "Done."
Stop-Transcript | Out-Null
