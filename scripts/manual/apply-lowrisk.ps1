# Applies the low-risk tweaks from docs/TWEAKS.md by hand (before the tool exists).
# Captures every original value to data/manual/backup-<timestamp>.json first,
# so scripts/manual/restore.ps1 can put everything back.
# Must run elevated.
param(
    [string]$Adapter = 'Wi-Fi',
    [switch]$ShowState   # print current values only, change nothing
)
$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outDir = Join-Path $root 'data\manual'
New-Item -ItemType Directory -Force $outDir | Out-Null
$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'

$SUB_WIRELESS = '19cbb8fa-5279-450e-9fac-8a3d5fedd0c1'; $SET_WIRELESS = '12bbebe6-58d6-4636-95bb-3217ef867c1a'
$SUB_PCIE     = '501a4d13-42af-4429-9fd1-a8218c268e20'; $SET_ASPM     = 'ee12f906-d277-404b-b6da-e5fa1a576df5'
$ADV_TARGETS  = [ordered]@{
    'Power Saving'          = 'Disabled'
    'Wake on Magic Packet'  = 'Disabled'
    'Wake on Pattern Match' = 'Disabled'
}

function Get-PowerIndex($sub, $set) {
    $text = (powercfg /q SCHEME_CURRENT $sub $set) -join "`n"
    $m = [regex]::Matches($text, 'Current (AC|DC) Power Setting Index: 0x([0-9a-fA-F]+)')
    if ($m.Count -lt 2) { throw "Cannot read powercfg $sub $set" }
    [ordered]@{ AC = [Convert]::ToInt32($m[0].Groups[2].Value, 16); DC = [Convert]::ToInt32($m[1].Groups[2].Value, 16) }
}

function Get-AdapterClassKey($name) {
    $guid = (Get-NetAdapter -Name $name).InterfaceGuid
    $cls  = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}'
    Get-ChildItem $cls -ErrorAction SilentlyContinue |
        Where-Object { (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).NetCfgInstanceId -eq $guid } |
        Select-Object -First 1
}

function Get-State {
    $adv = Get-NetAdapterAdvancedProperty -Name $Adapter
    $key = Get-AdapterClassKey $Adapter
    $pnp = (Get-ItemProperty $key.PSPath -ErrorAction SilentlyContinue).PnPCapabilities
    $advValues = [ordered]@{}
    foreach ($dn in $ADV_TARGETS.Keys) { $advValues[$dn] = ($adv | Where-Object DisplayName -eq $dn).DisplayValue }
    [ordered]@{
        adapter         = $Adapter
        scheme          = ((powercfg /getactivescheme) -join ' ').Trim()
        advanced        = $advValues
        classKey        = $key.PSPath
        pnpCapabilities = $pnp   # $null = value absent
        powerWireless   = Get-PowerIndex $SUB_WIRELESS $SET_WIRELESS
        powerAspm       = Get-PowerIndex $SUB_PCIE $SET_ASPM
    }
}

if ($ShowState) { Get-State | ConvertTo-Json -Depth 5; return }
Start-Transcript -Path (Join-Path $outDir "apply-$stamp.log") | Out-Null

# 1. Capture
$before = Get-State
$backupPath = Join-Path $outDir "backup-$stamp.json"
$before | ConvertTo-Json -Depth 5 | Set-Content -Path $backupPath -Encoding UTF8
Write-Host "Backup saved: $backupPath"

# 2. Apply (no restart per property; one adapter restart at the end)
foreach ($dn in $ADV_TARGETS.Keys) {
    if ($before.advanced[$dn] -ne $ADV_TARGETS[$dn]) {
        Write-Host "Set '$dn' -> $($ADV_TARGETS[$dn])"
        Set-NetAdapterAdvancedProperty -Name $Adapter -DisplayName $dn -DisplayValue $ADV_TARGETS[$dn] -NoRestart
    }
}

$pnpOld = if ($null -eq $before.pnpCapabilities) { 0 } else { [int]$before.pnpCapabilities }
$pnpNew = $pnpOld -bor 0x18
if ($pnpNew -ne $pnpOld -or $null -eq $before.pnpCapabilities) {
    Write-Host "Set PnPCapabilities $pnpOld -> $pnpNew"
    Set-ItemProperty -Path $before.classKey -Name PnPCapabilities -Value $pnpNew -Type DWord
}

Write-Host "Set Wireless Adapter power mode AC/DC -> 0 (Maximum Performance)"
powercfg /setacvalueindex SCHEME_CURRENT $SUB_WIRELESS $SET_WIRELESS 0
powercfg /setdcvalueindex SCHEME_CURRENT $SUB_WIRELESS $SET_WIRELESS 0
Write-Host "Set PCIe ASPM AC/DC -> 0 (Off)"
powercfg /setacvalueindex SCHEME_CURRENT $SUB_PCIE $SET_ASPM 0
powercfg /setdcvalueindex SCHEME_CURRENT $SUB_PCIE $SET_ASPM 0
powercfg /setactive SCHEME_CURRENT

# 3. Restart adapter so driver properties and PnPCapabilities take effect
Write-Host "Restarting adapter '$Adapter'..."
Restart-NetAdapter -Name $Adapter
$deadline = (Get-Date).AddSeconds(60)
do {
    Start-Sleep -Seconds 2
    $connected = (netsh wlan show interfaces) -match '^\s*State\s*:\s*connected'
} until ($connected -or (Get-Date) -gt $deadline)
Write-Host ("Wi-Fi reconnected: {0}" -f [bool]$connected)

# 4. Verify
$after = Get-State
$after | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $outDir "after-$stamp.json") -Encoding UTF8
Write-Host "--- BEFORE ---"; $before | ConvertTo-Json -Depth 5
Write-Host "--- AFTER ---";  $after  | ConvertTo-Json -Depth 5
Stop-Transcript | Out-Null
