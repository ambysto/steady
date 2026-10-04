# Lightweight background logger (stand-in until the real tool exists).
# Every second: ping router (auto-detected gateway), 1.1.1.1, 8.8.8.8.
# Every 5 seconds: read Wi-Fi state via netsh.
# Writes to data/monitor/:
#   pinglog-YYYYMMDD.csv  per-minute stats per target
#   wifi-YYYYMMDD.csv     per-minute Wi-Fi signal / channel / BSSID
#   events-YYYYMMDD.csv   outages, Wi-Fi state changes, roaming
# Does not need admin. Single instance (logger.pid).
# Stop:  Stop-Process -Id (Get-Content data\monitor\logger.pid)
param(
    [string]$OutDir,
    [int]$IntervalMs = 1000
)
$ErrorActionPreference = 'Continue'

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutDir) { $OutDir = Join-Path $root 'data\monitor' }
New-Item -ItemType Directory -Force $OutDir | Out-Null

$pidFile = Join-Path $OutDir 'logger.pid'
if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    # A PID alone proves nothing: after a reboot the number may belong to an unrelated process.
    # Only treat it as "already running" when that process is really this script.
    if ($old -match '^\d+$') {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$old" -ErrorAction SilentlyContinue
        if ($p -and $p.CommandLine -match 'ping-logger\.ps1') { Write-Host "Logger already running (PID $old)"; exit 0 }
    }
}
Set-Content -Path $pidFile -Value $PID

function Get-Gateway {
    $r = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -ne '0.0.0.0' } |
        Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
    if ($r) { $r.NextHop } else { $null }
}

function Get-Wifi {
    $h = @{}
    foreach ($line in (netsh wlan show interfaces)) {
        if ($line -match '^\s*([^:]+?)\s*:\s*(.*)$') {
            $k = $matches[1].Trim()
            if (-not $h.ContainsKey($k)) { $h[$k] = $matches[2].Trim() }
        }
    }
    [pscustomobject]@{
        state   = $h['State']; ssid = $h['SSID']; bssid = $h['AP BSSID']; channel = $h['Channel']
        signal  = ($h['Signal'] -replace '%', ''); rssi = $h['Rssi']
        rx      = $h['Receive rate (Mbps)']; tx = $h['Transmit rate (Mbps)']
    }
}

function Add-Row($prefix, $header, $line) {
    $file = Join-Path $OutDir ("{0}-{1}.csv" -f $prefix, (Get-Date -Format 'yyyyMMdd'))
    if (-not (Test-Path $file)) { Add-Content -Path $file -Value $header -Encoding UTF8 }
    Add-Content -Path $file -Value $line -Encoding UTF8
}

function Write-Ev($kind, $detail, $duration) {
    Add-Row 'events' 'time,kind,duration_s,detail' ('{0},{1},{2},"{3}"' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $kind, $duration, ($detail -replace '"', "'"))
}

$ping    = New-Object System.Net.NetworkInformation.Ping
$targets = [ordered]@{ router = (Get-Gateway); cloudflare = '1.1.1.1'; google = '8.8.8.8' }
$names   = @($targets.Keys)

function New-Buckets {
    $b = @{}
    foreach ($k in $names) { $b[$k] = @{ sent = 0; lost = 0; rtts = New-Object System.Collections.Generic.List[double] } }
    $b
}
$buckets  = New-Buckets
$signals  = New-Object System.Collections.Generic.List[double]
$streak   = @{ router = 0; internet = 0 }
$outage   = @{ router = $null; internet = $null }
$inetRouterOverlap = $false   # did the router also fail during the current internet outage?
$curMin   = Get-Date -Format 'yyyy-MM-dd HH:mm'
$wifi     = Get-Wifi
$nextWifi = Get-Date

Write-Ev 'logger_start' ("PID $PID, gateway " + $targets.router) ''

try {
    while ($true) {
        $tick = Get-Date
        $ok = @{}
        foreach ($k in $names) {
            $ip = $targets[$k]; $b = $buckets[$k]; $b.sent++
            $rtt = $null
            if ($ip) { try { $r = $ping.Send($ip, 900); if ($r.Status -eq 'Success') { $rtt = [double]$r.RoundtripTime } } catch { } }
            if ($null -ne $rtt) { $b.rtts.Add($rtt); $ok[$k] = $true } else { $b.lost++; $ok[$k] = $false }
        }

        # Outage detection: 3 consecutive failures opens an outage, first success closes it.
        if ($ok['router']) {
            if ($outage.router) { Write-Ev 'router_down' "PC could not reach router $($targets.router)" ([math]::Round(($tick - $outage.router).TotalSeconds)); $outage.router = $null }
            $streak.router = 0
        } else {
            $streak.router++
            if ($streak.router -eq 3) { $outage.router = $tick.AddSeconds(-2) }
        }
        if ($ok['cloudflare'] -or $ok['google']) {
            if ($outage.internet) {
                $why = if ($inetRouterOverlap) { 'router also unreachable' } else { 'router reachable -> ISP/router WAN side' }
                Write-Ev 'internet_down' $why ([math]::Round(($tick - $outage.internet).TotalSeconds)); $outage.internet = $null
            }
            $streak.internet = 0
        } else {
            $streak.internet++
            if ($streak.internet -eq 3) { $outage.internet = $tick.AddSeconds(-2); $inetRouterOverlap = $false }
            if ($outage.internet -and $streak.router -gt 0) { $inetRouterOverlap = $true }
        }

        # Wi-Fi state every 5s
        if ($tick -ge $nextWifi) {
            $nextWifi = $tick.AddSeconds(5)
            $w = Get-Wifi
            if ($w.state -ne $wifi.state) { Write-Ev 'wifi_state' ("{0} -> {1} ({2})" -f $wifi.state, $w.state, $w.ssid) '' ; $targets.router = Get-Gateway }
            elseif ($w.state -eq 'connected' -and $wifi.bssid -and $w.bssid -ne $wifi.bssid) { Write-Ev 'roam' ("{0} -> {1} ch{2}" -f $wifi.bssid, $w.bssid, $w.channel) '' }
            if ($w.signal -match '^\d+$') { $signals.Add([double]$w.signal) }
            $wifi = $w
        }

        # Minute rollover
        $nowMin = Get-Date -Format 'yyyy-MM-dd HH:mm'
        if ($nowMin -ne $curMin) {
            foreach ($k in $names) {
                $b = $buckets[$k]; $r = $b.rtts
                $avg = ''; $max = ''; $jit = ''
                if ($r.Count -gt 0) {
                    $m = $r | Measure-Object -Average -Maximum
                    $avg = [math]::Round($m.Average, 1); $max = $m.Maximum
                    if ($r.Count -gt 1) { $s = 0; for ($i = 1; $i -lt $r.Count; $i++) { $s += [math]::Abs($r[$i] - $r[$i - 1]) }; $jit = [math]::Round($s / ($r.Count - 1), 1) }
                }
                Add-Row 'pinglog' 'time,target,ip,sent,lost,avg_ms,max_ms,jitter_ms' ("{0},{1},{2},{3},{4},{5},{6},{7}" -f $curMin, $k, $targets[$k], $b.sent, $b.lost, $avg, $max, $jit)
            }
            $sigAvg = if ($signals.Count) { [math]::Round(($signals | Measure-Object -Average).Average, 1) } else { '' }
            Add-Row 'wifi' 'time,state,ssid,bssid,channel,signal_avg,rssi,rx_mbps,tx_mbps' ('{0},{1},"{2}",{3},{4},{5},{6},{7},{8}' -f $curMin, $wifi.state, $wifi.ssid, $wifi.bssid, $wifi.channel, $sigAvg, $wifi.rssi, $wifi.rx, $wifi.tx)
            $buckets = New-Buckets; $signals.Clear(); $curMin = $nowMin
            $gw = Get-Gateway; if ($gw) { $targets.router = $gw }
        }

        $elapsed = ((Get-Date) - $tick).TotalMilliseconds
        if ($elapsed -lt $IntervalMs) { Start-Sleep -Milliseconds ([int]($IntervalMs - $elapsed)) }
    }
}
finally {
    Write-Ev 'logger_stop' "PID $PID" ''
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
