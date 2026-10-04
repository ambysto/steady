# Summarises network stability since a point in time (default: newest manual tweak backup)
# and compares Windows WLAN event rates against the N days before it.
# Usage:  .\report.ps1                    .\report.ps1 -Since '2026-10-03 11:00'
param(
    [datetime]$Since,
    [int]$BaselineDays = 7
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not $Since) {
    $latest = Get-ChildItem (Join-Path $root 'data\manual') -Filter 'backup-*.json' -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $latest) { throw 'No -Since given and no data\manual\backup-*.json found.' }
    $Since = [datetime]::ParseExact(($latest.BaseName -replace '^backup-', ''), 'yyyyMMdd-HHmmss', $null)
}
$now = Get-Date

function Get-WlanStats($from, $to) {
    $ev  = @(Get-WinEvent -FilterHashtable @{ LogName = 'Microsoft-Windows-WLAN-AutoConfig/Operational'; Id = 8003; StartTime = $from; EndTime = $to } -ErrorAction SilentlyContinue)
    $ihv = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WLAN-AutoConfig'; Id = 10002; StartTime = $from; EndTime = $to } -ErrorAction SilentlyContinue)
    $lim = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Microsoft-Windows-WLAN-AutoConfig'; Id = 4003; StartTime = $from; EndTime = $to } -ErrorAction SilentlyContinue)
    $hours = [math]::Max(($to - $from).TotalHours, 0.01)
    $byDriver = @($ev | Where-Object { $_.Message -match 'disconnected by the driver' }).Count
    [pscustomobject]@{
        Period          = '{0:yyyy-MM-dd HH:mm} -> {1:yyyy-MM-dd HH:mm}' -f $from, $to
        Hours           = [math]::Round($hours, 1)
        Disconnects     = $ev.Count
        ByDriver        = $byDriver
        DriverPer24h    = [math]::Round($byDriver * 24 / $hours, 2)
        IhvCrash        = $ihv.Count
        IhvPer24h       = [math]::Round($ihv.Count * 24 / $hours, 2)
        LimitedConn     = $lim.Count
    }
}

Write-Host "=== Windows WLAN events: before vs after $($Since.ToString('yyyy-MM-dd HH:mm')) ===" -ForegroundColor Cyan
@(
    (Get-WlanStats $Since.AddDays(-$BaselineDays) $Since) | Add-Member -PassThru NoteProperty Label 'BEFORE'
    (Get-WlanStats $Since $now)                          | Add-Member -PassThru NoteProperty Label 'AFTER'
) | Format-Table Label, Period, Hours, Disconnects, ByDriver, DriverPer24h, IhvCrash, IhvPer24h, LimitedConn -AutoSize | Out-String -Width 220

$mon = Join-Path $root 'data\monitor'
$rows = @(Get-ChildItem $mon -Filter 'pinglog-*.csv' -ErrorAction SilentlyContinue | ForEach-Object { Import-Csv $_.FullName } | Where-Object { [datetime]$_.time -ge $Since })
if ($rows.Count) {
    Write-Host "=== Ping log since $($Since.ToString('yyyy-MM-dd HH:mm')) ($(($rows | Select-Object -ExpandProperty time -Unique).Count) minutes) ===" -ForegroundColor Cyan
    $rows | Group-Object target | ForEach-Object {
        $sent = ($_.Group | Measure-Object sent -Sum).Sum; $lost = ($_.Group | Measure-Object lost -Sum).Sum
        $ok   = @($_.Group | Where-Object { $_.avg_ms -ne '' })
        $wavg = if ($ok.Count) { ($ok | ForEach-Object { [double]$_.avg_ms * ([int]$_.sent - [int]$_.lost) } | Measure-Object -Sum).Sum / [math]::Max(($ok | ForEach-Object { [int]$_.sent - [int]$_.lost } | Measure-Object -Sum).Sum, 1) } else { 0 }
        [pscustomobject]@{
            Target     = $_.Name
            Sent       = $sent
            Lost       = $lost
            'Loss%'    = [math]::Round(100 * $lost / [math]::Max($sent, 1), 2)
            AvgMs      = [math]::Round($wavg, 1)
            MaxMs      = ($ok | ForEach-Object { [double]$_.max_ms } | Measure-Object -Maximum).Maximum
            JitterMs   = [math]::Round((($ok | Where-Object { $_.jitter_ms -ne '' } | ForEach-Object { [double]$_.jitter_ms } | Measure-Object -Average).Average), 1)
            BadMinutes = @($_.Group | Where-Object { [int]$_.lost -gt 0 }).Count
        }
    } | Format-Table -AutoSize | Out-String -Width 200
}

$events = @(Get-ChildItem $mon -Filter 'events-*.csv' -ErrorAction SilentlyContinue | ForEach-Object { Import-Csv $_.FullName } | Where-Object { [datetime]$_.time -ge $Since })
if ($events.Count) {
    Write-Host "=== Logger events ===" -ForegroundColor Cyan
    $events | Group-Object kind | ForEach-Object {
        [pscustomobject]@{ Kind = $_.Name; Count = $_.Count; TotalSec = ($_.Group | Where-Object { $_.duration_s -ne '' } | Measure-Object duration_s -Sum).Sum }
    } | Format-Table -AutoSize | Out-String
    Write-Host "Last 15:"
    $events | Select-Object -Last 15 | Format-Table time, kind, duration_s, detail -AutoSize | Out-String -Width 220
}
