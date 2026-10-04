# Sets the Wi-Fi card's PHY mode (tweak wifi_mode_ac in docs/TWEAKS.md).
#   .\set-phymode.ps1 -Mode ac     force Wi-Fi 5 (802.11ac)
#   .\set-phymode.ps1 -Mode ax     back to the driver default (802.11ax)
# Records before/after in data\manual\phymode-<timestamp>.log. Must run elevated.
param(
    [Parameter(Mandatory)][ValidateSet('ax', 'ac')][string]$Mode,
    [string]$Adapter = 'Wi-Fi'
)
$ErrorActionPreference = 'Stop'

$root   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$outDir = Join-Path $root 'data\manual'
New-Item -ItemType Directory -Force $outDir | Out-Null
Start-Transcript -Path (Join-Path $outDir ("phymode-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))) | Out-Null

$prop = Get-NetAdapterAdvancedProperty -Name $Adapter | Where-Object { $_.DisplayName -match '^802\.11ax/ac' } | Select-Object -First 1
if (-not $prop) { throw "Adapter '$Adapter' has no 802.11ax/ac/n/abg property." }
$target = $prop.ValidDisplayValues | Where-Object { $_ -match "802\.11$Mode`$" } | Select-Object -First 1
if (-not $target) { throw "No value for 802.11$Mode in: $($prop.ValidDisplayValues -join ', ')" }

Write-Host "Before: '$($prop.DisplayName)' = $($prop.DisplayValue)"
if ($prop.DisplayValue -ne $target) {
    Set-NetAdapterAdvancedProperty -Name $Adapter -DisplayName $prop.DisplayName -DisplayValue $target   # restarts the adapter
    $deadline = (Get-Date).AddSeconds(60)
    do { Start-Sleep -Seconds 2 } until (((netsh wlan show interfaces) -match '^\s*State\s*:\s*connected') -or (Get-Date) -gt $deadline)
}
$now = (Get-NetAdapterAdvancedProperty -Name $Adapter -DisplayName $prop.DisplayName).DisplayValue
Write-Host "After:  '$($prop.DisplayName)' = $now"
netsh wlan show interfaces | Select-String 'State|SSID|Channel|Radio type|Rssi' | ForEach-Object { Write-Host $_.Line.Trim() }
Stop-Transcript | Out-Null
