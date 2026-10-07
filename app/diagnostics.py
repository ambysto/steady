"""Diagnostics: 13 read-only checks, each returning ok / warn / bad / info.

Every check is split in two:
  * `evaluate_*`  pure function over already-collected data - this is what the tests exercise
  * `check_*`     pulls the data from a Context (lazy, cached per run) and calls evaluate_*

A check that cannot get its data reports `info` with the reason, never `ok`.
See docs/DIAGNOSTICS.md for the thresholds and how they were calibrated.

Text is stored as messages ({"key", "params"}, ADR-0006), not as translated strings, so a saved
run shows in whatever language is chosen when it is read. Keys live under "diag." in
app/locales/*.json; `CheckResult.localized()` / `i18n.localize()` turn them into text.

    python -m app.diagnostics                 run everything, save the run, print a summary
    python -m app.diagnostics --compare       also diff against the previous saved run
    python -m app.diagnostics --only 2,13 --json --no-save
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Iterable

from . import config, dnsprobe, i18n, winutil
from .i18n import msg
from .storage import Storage

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
_ORDER = {OK: 0, INFO: 1, WARN: 2, BAD: 3}
_BETTER_RANK = {OK: 0, INFO: 0, WARN: 1, BAD: 2}

PUBLIC_DNS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
HISTORY_HOURS = 24

Message = Any   # a msg() dict, or plain text for content that has no words (or old saved runs)


@dataclass
class CheckResult:
    id: int
    key: str
    title: Message
    status: str
    summary: Message
    details: list[Message] = field(default_factory=list)
    advice: Message = ""
    tweak: str | None = None      # id of the related tweak, if any
    error: str | None = None      # set when the check could not run

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def localized(self, lang: str | None = None) -> "CheckResult":
        """Copy with every message rendered as text."""
        return replace(self, title=i18n.render(self.title, lang), summary=i18n.render(self.summary, lang),
                       details=[i18n.render(x, lang) for x in self.details], advice=i18n.render(self.advice, lang))


def title_of(key: str) -> Message:
    return msg(f"diag.{key}.title")


def worst(statuses: list[str]) -> str:
    return max(statuses, key=_ORDER.__getitem__, default=OK)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _ago(now: float, ts: float) -> Message:
    secs = max(0, now - ts)
    if secs < 3600:
        return msg("diag.ago.minutes", count=max(1, int(secs // 60)))
    if secs < 48 * 3600:
        return msg("diag.ago.hours", count=int(secs // 3600))
    return msg("diag.ago.days", count=int(secs // 86400))


# --- MAC helpers --------------------------------------------------------------------

def _octets(bssid: str) -> list[int]:
    try:
        return [int(p, 16) for p in bssid.split(":")]
    except ValueError:
        return []


def device_key(bssid: str) -> str:
    """First 5 octets: virtual APs / bands of one router share them."""
    return ":".join(f"{o:02x}" for o in _octets(bssid)[:5])


def family_key(bssid: str) -> str:
    """First 3 octets with the locally-administered bit cleared (c4:… and c6:… are one family)."""
    o = _octets(bssid)[:3]
    if len(o) == 3:
        o[0] &= ~0x02
    return ":".join(f"{x:02x}" for x in o)


def _ssid_label(ssid: str) -> Message:
    return ssid or msg("diag.common.hidden_ssid")


# --- history join (monitor data) -----------------------------------------------------

@dataclass(frozen=True)
class Minute:
    ts: int
    rssi: int
    rx_mbps: float
    router_loss_pct: float


def join_minutes(wifi_rows: list[dict], ping_rows: list[dict], min_sent: int = 30) -> list[Minute]:
    """One record per connected minute that has both Wi-Fi numbers and enough router pings."""
    router = {r["ts"]: r for r in ping_rows if r["target"] == "router"}
    out = []
    for w in wifi_rows:
        p = router.get(w["ts"])
        if (p is None or w.get("state") != "connected" or w.get("rssi") is None or w.get("rx_mbps") is None
                or p["sent"] < min_sent):
            continue
        out.append(Minute(w["ts"], w["rssi"], w["rx_mbps"], 100.0 * p["lost"] / p["sent"]))
    return out


# --- 1. driver ------------------------------------------------------------------------

def _wifi_adapter(adapters: list[dict], interface: str | None = None) -> dict | None:
    """The real Wi-Fi card. "Microsoft Wi-Fi Direct Virtual Adapter" also reports Native 802.11
    media and is listed on some machines, so physical cards win, then the connected interface."""
    wifi = [a for a in adapters if winutil.is_wifi_adapter(a)]
    wifi.sort(key=lambda a: (bool(a.get("Virtual")), a.get("Name") != interface))
    return wifi[0] if wifi else None


def _connected_interface(ctx: "Context") -> str | None:
    try:
        w = ctx.get("wifi")
    except Exception:
        return None
    return w.interface if w is not None else None


def evaluate_driver(adapter: dict | None, ihv_stops: list[int], store: list[winutil.StoredDriver],
                    now: float, ihv_error: str | None = None) -> CheckResult:
    base = dict(id=1, key="driver", title=title_of("driver"))
    if adapter is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.driver.no_adapter"))
    details = [msg("diag.driver.version", description=adapter["InterfaceDescription"],
                   version=adapter["DriverVersion"], date=adapter["DriverDate"] or msg("diag.common.unknown_date"),
                   provider=adapter["DriverProvider"])]
    months = None
    if adapter.get("DriverDate"):
        age = now - datetime.strptime(adapter["DriverDate"], "%Y-%m-%d").timestamp()
        months = age / 86400 / 30.44
        details.append(msg("diag.driver.age", count=round(months)))
    same = [d for d in store if d.provider == adapter.get("DriverProvider")]
    for d in same:
        details.append(msg("diag.driver.in_store", name=d.published, version=d.version, date=d.date))

    status, summary = OK, msg("diag.driver.ok")
    if ihv_error:
        details.append(msg("diag.common.system_log_error", error=ihv_error))
        status, summary = INFO, msg("diag.driver.ihv_unknown")
    elif ihv_stops:
        last = max(ihv_stops)
        details.append(msg("diag.driver.ihv_detail", count=len(ihv_stops), time=_fmt_ts(last)))
        status = BAD
        summary = msg("diag.driver.ihv_bad", count=len(ihv_stops), ago=_ago(now, last))
    if months is not None and months > 12 and status in (OK, INFO):
        status, summary = WARN, msg("diag.driver.old", count=round(months))
    advice: Message = ""
    if status in (WARN, BAD):
        advice = msg("diag.driver.advice")
    return CheckResult(**base, status=status, summary=summary, details=details, advice=advice)


# --- 2. signal ------------------------------------------------------------------------

def evaluate_signal(wifi: winutil.WifiState | None) -> CheckResult:
    base = dict(id=2, key="signal", title=title_of("signal"))
    if wifi is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.common.no_wifi_card"))
    if not wifi.connected or wifi.rssi is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.signal.not_connected"))
    details = [msg("diag.signal.network", ssid=wifi.ssid, channel=wifi.channel, radio=wifi.radio_type),
               f"RSSI {wifi.rssi} dBm ({wifi.signal}%)",
               msg("diag.signal.rates", rx=wifi.rx_mbps, tx=wifi.tx_mbps)]
    if wifi.rssi >= -60:
        status, summary = OK, msg("diag.signal.good", rssi=wifi.rssi)
    elif wifi.rssi >= -70:
        status, summary = WARN, msg("diag.signal.fair", rssi=wifi.rssi)
    else:
        status, summary = BAD, msg("diag.signal.weak", rssi=wifi.rssi)
    advice = "" if status == OK else msg("diag.signal.advice")
    return CheckResult(**base, status=status, summary=summary, details=details, advice=advice)


# --- 3. channel interference ------------------------------------------------------------

_5G_GROUPS = {"36–48": (36, 40, 44, 48), "149–161": (149, 153, 157, 161)}


def evaluate_interference(wifi: winutil.WifiState | None, scan: list[winutil.ScanEntry],
                          floor: int = 10) -> CheckResult:
    base = dict(id=3, key="interference", title=title_of("interference"))
    if wifi is None or not wifi.connected or wifi.channel is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.interference.not_connected"))
    own = device_key(wifi.bssid)
    mine = next((e for e in scan if e.bssid == wifi.bssid), None)
    band = mine.band if mine else ("2.4 GHz" if wifi.channel <= 14 else "5 GHz")

    def foreign(e: winutil.ScanEntry) -> bool:
        return device_key(e.bssid) != own and (e.signal or 0) >= floor

    same = {(e.ssid or e.bssid): e for e in scan if foreign(e) and e.channel == wifi.channel and e.band == band}
    details: list[Message] = [msg("diag.interference.current", channel=wifi.channel, band=band, count=len(same))]
    details += [msg("diag.common.network_signal", ssid=_ssid_label(e.ssid), signal=e.signal)
                for e in sorted(same.values(), key=lambda e: -(e.signal or 0))]
    if len(same) < 2:
        return CheckResult(**base, status=OK, summary=msg("diag.interference.quiet", channel=wifi.channel,
                                                          count=len(same)), details=details)

    if band == "5 GHz":
        per_group = {}
        for name, chans in _5G_GROUPS.items():
            per_group[name] = {c: len({(e.ssid or e.bssid) for e in scan if foreign(e) and e.band == "5 GHz"
                                       and e.channel == c}) for c in chans}
        totals = {n: sum(v.values()) for n, v in per_group.items()}
        best = min(totals, key=totals.__getitem__)
        quiet = min(per_group[best], key=per_group[best].__getitem__)
        details.append(msg("diag.interference.groups", groups=[f"{n}: {c}" for n, c in totals.items()]))
        advice = msg("diag.interference.advice_5g", group=best, channel=quiet)
    elif band == "6 GHz":   # many free channels, no 5 GHz to move to (SIC-71)
        advice = msg("diag.interference.advice_6g")
    else:
        advice = msg("diag.interference.advice_24g")
    return CheckResult(**base, status=WARN, summary=msg("diag.interference.crowded", count=len(same),
                                                        channel=wifi.channel), details=details, advice=advice)


# --- 4. drop history ---------------------------------------------------------------------

def _classify_reason(reason: str) -> str:
    r = reason.lower()
    if "by the driver" in r:
        return "driver"
    if "by the user" in r or "user wants" in r:
        return "user"
    return "other"


def evaluate_drops(disconnects: list[dict], limited: list[int], error: str | None = None,
                   now: float | None = None) -> CheckResult:
    base = dict(id=4, key="drops", title=title_of("drops"))
    now = time.time() if now is None else now
    if error:
        return CheckResult(**base, status=INFO, summary=msg("diag.drops.log_error"), details=[error])
    by_reason: dict[str, int] = {}     # "" = no reason given
    driver_by_day: dict[str, int] = {}
    for d in disconnects:
        by_reason[d["reason"] or ""] = by_reason.get(d["reason"] or "", 0) + 1
        if _classify_reason(d["reason"]) == "driver":
            day = datetime.fromtimestamp(d["ts"]).strftime("%m-%d")
            driver_by_day[day] = driver_by_day.get(day, 0) + 1
    driver = sum(driver_by_day.values())
    details: list[Message] = [msg("diag.drops.reason", count=n, reason=reason or msg("diag.drops.no_reason"))
                              for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1])]
    if driver_by_day:
        details.append(msg("diag.drops.by_day", days=" · ".join(f"{d}: {n}" for d, n in sorted(driver_by_day.items()))))
    if limited:
        details.append(msg("diag.drops.limited", count=len(limited)))
    driver_ts = [d["ts"] for d in disconnects if _classify_reason(d["reason"]) == "driver"]
    if driver > 5:
        status = BAD
        summary = msg("diag.drops.bad", count=driver, ago=_ago(now, max(driver_ts)))
    elif driver or limited:
        status, summary = WARN, msg("diag.drops.warn", driver=driver, limited=len(limited))
    else:
        status, summary = OK, msg("diag.drops.ok")
    advice = "" if status == OK else msg("diag.drops.advice")
    return CheckResult(**base, status=status, summary=summary, details=details, advice=advice,
                       tweak="power_pcie_aspm_off" if status != OK else None)


# --- 5. ping quality ---------------------------------------------------------------------

def _loss_status(loss_pct: float) -> str:
    return BAD if loss_pct > 3 else WARN if loss_pct > 1 else OK


def _aggregate(rows: list[dict]) -> dict[str, dict[str, float]]:
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["target"], {"sent": 0, "lost": 0, "jitter": []})
        a["sent"] += r["sent"]
        a["lost"] += r["lost"]
        if r.get("jitter") is not None:
            a["jitter"].append(r["jitter"])
    return {t: {"sent": a["sent"], "lost": a["lost"],
                "loss": 100.0 * a["lost"] / a["sent"] if a["sent"] else 0.0,
                "jitter": statistics.fmean(a["jitter"]) if a["jitter"] else None} for t, a in agg.items()}


PROBE_PREFIXES = ("tcp_", "http_")   # target names of the monitor's TCP/HTTP probes (app/probe.py)


def _is_probe(target: str) -> bool:
    return target.startswith(PROBE_PREFIXES)


# Minutes left out of check #5 after a network change: the minute of the change and the next one.
# Around a switch (VPN on/off, another Wi-Fi network, a new router) pings fail or take two routes,
# and that says nothing about the line (GitHub issue #2).
CHANGE_MINUTES = 2
NETWORK_CHANGE_EVENTS = ("gateway_change", "route_change", "roam", "wifi_state")   # event kinds of app/monitor.py


def left_out_minutes(changes: Iterable[float]) -> set[int]:
    """Starts of the minutes left out for network changes at the given times (seconds since 1970)."""
    return {int(c) - int(c) % 60 + 60 * i for c in changes for i in range(CHANGE_MINUTES)}


def evaluate_ping(rows_1h: list[dict], rows_5m: list[dict], min_sent: int = 60,
                  min_probe_sent: int = 20, changes: Iterable[float] = ()) -> CheckResult:
    """`changes`: times of network changes; rows of the minutes around them (by `ts`) are left out."""
    base = dict(id=5, key="ping", title=title_of("ping"))
    left_out = left_out_minutes(changes)
    skipped = len({r["ts"] for r in rows_1h if r.get("ts") in left_out})
    rows_1h = [r for r in rows_1h if r.get("ts") not in left_out]
    rows_5m = [r for r in rows_5m if r.get("ts") not in left_out]
    note = [msg("diag.ping.left_out", count=skipped)] if skipped else []
    agg = _aggregate(rows_1h)
    icmp_hour = {t: a for t, a in agg.items() if not _is_probe(t) and a["sent"] >= min_sent}
    probe_hour = {t: a for t, a in agg.items() if _is_probe(t) and a["sent"] >= min_probe_sent}
    if not icmp_hour and not probe_hour:
        return CheckResult(**base, status=INFO, summary=msg("diag.ping.no_data"), details=note)
    recent = _aggregate(rows_5m)
    statuses: dict[str, str] = {}
    lossy: set[str] = set()   # targets whose loss alone warns; the others warn for jitter only
    details: list[Message] = []
    for target, a in sorted(icmp_hour.items(), key=lambda kv: (kv[0] != "router", kv[0])):
        st = _loss_status(a["loss"])
        if st in (WARN, BAD):
            lossy.add(target)
        if a["jitter"] is not None and a["jitter"] > 30:
            st = worst([st, WARN])
        statuses[target] = st
        r = recent.get(target)
        details.append(msg("diag.ping.line", target=target, loss=a["loss"], lost=int(a["lost"]), sent=int(a["sent"]),
                           jitter=msg("diag.ping.jitter", jitter=a["jitter"]) if a["jitter"] is not None else "",
                           recent=msg("diag.ping.recent", loss=r["loss"]) if r and r["sent"] else ""))
    for target, a in sorted(probe_hour.items()):
        details.append(msg("diag.ping.probe_line", target=target, loss=a["loss"], lost=int(a["lost"]),
                           sent=int(a["sent"])))
    details += note

    # "Does real traffic work?" = the best probe, the same any-one-is-enough rule the monitor uses:
    # one target blocking port 443 must not raise an alarm.
    probe_best = min((a["loss"] for a in probe_hour.values()), default=None)
    probes_ok = probe_best is not None and probe_best <= 1.0
    router = statuses.get("router")
    icmp_internet = worst([s for t, s in statuses.items() if t != "router"])
    icmp_limited = router not in (WARN, BAD) and icmp_internet in (WARN, BAD) and probes_ok
    if icmp_limited:  # ping to the Internet is lossy but real connections are fine: not a fault
        for t in statuses:
            if t != "router":
                statuses[t] = INFO
    overall = worst(list(statuses.values()) + ([_loss_status(probe_best)] if probe_best is not None else []))

    # Unstable latency without loss is said as such, not as packet loss (SIC-66).
    internet_lossy = any(t != "router" for t in lossy)
    if router in (WARN, BAD):
        summary = msg("diag.ping.router_loss" if "router" in lossy else "diag.ping.router_jitter")
    elif icmp_limited:
        summary = msg("diag.ping.icmp_limited" if internet_lossy else "diag.ping.icmp_jitter")
    elif probe_best is not None and probe_best > 1.0:
        summary = msg("diag.ping.wan_loss")
    elif icmp_internet in (WARN, BAD):
        summary = msg("diag.ping.wan_loss_ping_only" if internet_lossy else "diag.ping.wan_jitter")
    else:
        summary = msg("diag.ping.ok")
    advice: Message = ""
    if router in (WARN, BAD):
        advice = msg("diag.ping.advice_router")
    elif icmp_limited:
        advice = msg("diag.ping.advice_icmp_limited")
    elif overall != OK:
        advice = msg("diag.ping.advice_wan")
    return CheckResult(**base, status=overall, summary=summary, details=details, advice=advice)


# --- 6. DNS benchmark --------------------------------------------------------------------

DNS_ROLES = ("in_use", "in_use_router", "router", "public")   # values of the `labels` map


def _dns_label(labels: dict[str, str], server: str) -> Message:
    role = labels.get(server)
    if role in DNS_ROLES:
        return msg(f"diag.dns.role.{role}")
    return role or server


def evaluate_dns(bench: list[dnsprobe.ServerBenchmark], in_use: list[str], labels: dict[str, str]) -> CheckResult:
    """labels: server -> role (one of DNS_ROLES)."""
    base = dict(id=6, key="dns", title=title_of("dns"))
    if not bench:
        return CheckResult(**base, status=INFO, summary=msg("diag.dns.no_servers"))
    details = []
    for b in bench:
        median = msg("diag.common.ms", value=b.median_ms) if b.median_ms is not None else msg("diag.dns.no_reply")
        details.append(msg("diag.dns.line", label=_dns_label(labels, b.server), server=b.server, median=median,
                           failures=b.failures, sent=b.sent))
    used = [b for b in bench if b.server in in_use]
    if not used:
        return CheckResult(**base, status=INFO, summary=msg("diag.dns.unknown_in_use"), details=details)
    broken = [b for b in used if b.failures]
    candidates = [b for b in bench if b.median_ms is not None and not b.failures]
    fastest = min(candidates, key=lambda b: b.median_ms) if candidates else None
    primary = used[0]
    if broken:
        return CheckResult(**base, status=WARN, summary=msg("diag.dns.broken", servers=[b.server for b in broken]),
                           details=details, advice=msg("diag.dns.advice_broken"))
    if fastest and primary.median_ms is not None and primary.median_ms - fastest.median_ms > 20:
        gap = primary.median_ms - fastest.median_ms
        if fastest.server in in_use:
            advice = msg("diag.dns.advice_promote", server=fastest.server)
        elif labels.get(fastest.server) == "router":
            advice = msg("diag.dns.advice_router_cache")
        else:
            advice = msg("diag.dns.advice_consider", server=fastest.server)
        return CheckResult(**base, status=INFO, summary=msg("diag.dns.slower", server=fastest.server, gap=gap),
                           details=details, advice=advice)
    return CheckResult(**base, status=OK, summary=msg("diag.dns.ok"), details=details)


# --- 7. TCP port exhaustion ---------------------------------------------------------------

def evaluate_tcp(port_events: list[int], time_wait: int | None, error: str | None = None) -> CheckResult:
    base = dict(id=7, key="tcp_ports", title=title_of("tcp_ports"))
    if error:
        return CheckResult(**base, status=INFO, summary=msg("diag.tcp_ports.log_error"), details=[error])
    details: list[Message] = [msg("diag.tcp_ports.time_wait",
                                  value=time_wait if time_wait is not None else msg("diag.common.unreadable"))]
    if port_events:
        details.append(msg("diag.tcp_ports.events", count=len(port_events), time=_fmt_ts(max(port_events))))
        return CheckResult(**base, status=WARN, summary=msg("diag.tcp_ports.warn", count=len(port_events)),
                           details=details, advice=msg("diag.tcp_ports.advice"), tweak="tcp_timedwait")
    return CheckResult(**base, status=OK, summary=msg("diag.tcp_ports.ok"), details=details)


# --- 8. VPN / virtual adapters ---------------------------------------------------------------

_VPN_RE = re.compile(r"VPN|WireGuard|Wintun|\bTAP\b|TAP-|OpenVPN|NetBird|Surfshark|Tailscale|ZeroTier|NordLynx|"
                     r"Proton|WARP|Fortinet|Cisco AnyConnect|Cloudflare", re.I)


def evaluate_vpn(adapters: list[dict]) -> CheckResult:
    base = dict(id=8, key="vpn", title=title_of("vpn"))
    vpn = [a for a in adapters if _VPN_RE.search(f"{a.get('Name', '')} {a.get('InterfaceDescription', '')}")]
    up = [a for a in vpn if a.get("Status") == "Up"]
    details = [f"{a['Name']} ({a['InterfaceDescription']}): {a['Status']}" for a in vpn]
    if up:
        return CheckResult(**base, status=INFO, summary=msg("diag.vpn.up", count=len(up)),
                           details=details, advice=msg("diag.vpn.advice"))
    if vpn:
        return CheckResult(**base, status=OK, summary=msg("diag.vpn.off"), details=details)
    return CheckResult(**base, status=OK, summary=msg("diag.vpn.none"))


# --- 9. wired available ---------------------------------------------------------------------

def evaluate_wired(adapters: list[dict], wifi: winutil.WifiState | None) -> CheckResult:
    base = dict(id=9, key="wired", title=title_of("wired"))
    wired = [a for a in adapters if "802.3" in str(a.get("PhysicalMediaType", "")) and not a.get("Virtual")
             and "bluetooth" not in str(a.get("InterfaceDescription", "")).lower()]
    if not wired:
        return CheckResult(**base, status=OK, summary=msg("diag.wired.no_port"))
    details = [f"{a['Name']} ({a['InterfaceDescription']}): {a['Status']}" for a in wired]
    if any(a.get("Status") == "Up" for a in wired):
        return CheckResult(**base, status=OK, summary=msg("diag.wired.plugged"), details=details)
    if wifi is not None and wifi.connected:
        return CheckResult(**base, status=INFO, summary=msg("diag.wired.idle_port"),
                           details=details, advice=msg("diag.wired.advice"))
    return CheckResult(**base, status=INFO, summary=msg("diag.wired.unplugged"), details=details)


# --- 10. tweaks not enabled ------------------------------------------------------------------

def evaluate_tweaks(states: dict[str, dict] | None) -> CheckResult:
    base = dict(id=10, key="tweaks", title=title_of("tweaks"))
    if states is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.tweaks.unknown"))
    todo = sorted(tid for tid, s in states.items()
                  if s.get("risk") == "low" and s.get("supported", True) and not s.get("enabled"))
    if not todo:
        return CheckResult(**base, status=OK, summary=msg("diag.tweaks.ok"))
    return CheckResult(**base, status=INFO, summary=msg("diag.tweaks.todo", count=len(todo)),
                       details=list(todo), advice=msg("diag.tweaks.advice"))


# --- 11. Wi-Fi 7 / MLO ---------------------------------------------------------------------

_WIFI7_CARD_RE = re.compile(r"Wi-?Fi ?7|\bBE\d{3}\b|MT7925|RZ7\d\d|WCN7850|802\.11be", re.I)
MLO_MIN_MINUTES = 5
MLO_MIN_FRACTION = 0.25


def evaluate_mlo(wifi: winutil.WifiState | None, adapter: dict | None, scan: list[winutil.ScanEntry],
                 minutes: list[Minute], now: float | None = None) -> CheckResult:
    base = dict(id=11, key="wifi7_mlo", title=title_of("wifi7_mlo"))
    if wifi is None or not wifi.connected:
        return CheckResult(**base, status=INFO, summary=msg("diag.common.wifi_not_connected"))
    own = device_key(wifi.bssid)
    be = [e for e in scan if device_key(e.bssid) == own and e.radio_type == "802.11be"]
    if not be:
        return CheckResult(**base, status=OK, summary=msg("diag.wifi7_mlo.router_no_be"))
    desc = (adapter or {}).get("InterfaceDescription", "")
    card_w7 = wifi.radio_type == "802.11be" or bool(_WIFI7_CARD_RE.search(desc))
    if card_w7:
        return CheckResult(**base, status=OK, summary=msg("diag.wifi7_mlo.both"))

    bands = sorted({e.band for e in scan if e.ssid == wifi.ssid and device_key(e.bssid) == own})
    our_ssid_be = any(e.ssid == wifi.ssid for e in be)
    collapsed = [m for m in minutes if m.rx_mbps <= 6 and m.rssi >= -70 and m.router_loss_pct >= 5]
    frac = len(collapsed) / len(minutes) if minutes else 0.0
    details: list[Message] = [
        msg("diag.wifi7_mlo.card", description=desc or msg("diag.common.unknown"), radio=wifi.radio_type),
        msg("diag.wifi7_mlo.be_networks",
            networks=[msg("diag.common.network_band", ssid=_ssid_label(e.ssid), band=e.band) for e in be]),
        msg("diag.wifi7_mlo.bands", ssid=wifi.ssid, bands=bands or msg("diag.common.unknown")),
        msg("diag.wifi7_mlo.history", hours=HISTORY_HOURS, bad=len(collapsed), total=len(minutes), fraction=frac)]
    still_happening = True
    if now is not None:
        recent = [m for m in minutes if m.ts >= now - RECENT_WINDOW_S]
        if len(recent) >= RECENT_MIN_MINUTES:
            recent_bad = sum(1 for m in recent if m.rx_mbps <= 6 and m.rssi >= -70 and m.router_loss_pct >= 5)
            still_happening = recent_bad / len(recent) >= 0.05
            details.append(msg("diag.wifi7_mlo.recent", bad=recent_bad, total=len(recent)))
    if len(collapsed) >= MLO_MIN_MINUTES and frac >= MLO_MIN_FRACTION and still_happening:
        where = msg("diag.wifi7_mlo.where_ssid" if our_ssid_be else "diag.wifi7_mlo.where_router")
        return CheckResult(**base, status=WARN, summary=msg("diag.wifi7_mlo.warn", where=where),
                           details=details, advice=msg("diag.wifi7_mlo.advice_warn"))
    note = msg("diag.wifi7_mlo.note_same_ssid" if our_ssid_be else "diag.wifi7_mlo.note_other_ssid")
    return CheckResult(**base, status=INFO, summary=msg("diag.wifi7_mlo.info", note=note),
                       details=details, advice=msg("diag.wifi7_mlo.advice_info"))


# --- 12. modem Wi-Fi in bridge mode -------------------------------------------------------

def evaluate_modem_wifi(wifi: winutil.WifiState | None, scan: list[winutil.ScanEntry],
                        floor: int = 10) -> CheckResult:
    base = dict(id=12, key="modem_wifi", title=title_of("modem_wifi"))
    own = device_key(wifi.bssid) if wifi is not None and wifi.connected else None
    own_ssid = wifi.ssid if wifi is not None and wifi.connected else None
    foreign = [e for e in scan if (e.signal or 0) >= floor and e.ssid != own_ssid
               and (own is None or device_key(e.bssid) != own)]
    families: dict[str, list[winutil.ScanEntry]] = {}
    for e in foreign:
        families.setdefault(family_key(e.bssid), []).append(e)
    multi = {f: es for f, es in families.items() if len({e.bssid for e in es}) >= 2}

    def network(e: winutil.ScanEntry) -> Message:
        return msg("diag.modem_wifi.network", ssid=_ssid_label(e.ssid), band=e.band, channel=e.channel,
                   signal=e.signal)

    def describe(f: str, es: list[winutil.ScanEntry]) -> Message:
        return msg("diag.modem_wifi.family", family=f,
                   networks=[network(e) for e in sorted(es, key=lambda e: -(e.signal or 0))])

    advice = msg("diag.modem_wifi.advice")
    strong = {f: es for f, es in multi.items() if max(e.signal or 0 for e in es) >= 70}
    if strong:
        return CheckResult(**base, status=WARN, summary=msg("diag.modem_wifi.strong_family"),
                           details=[describe(f, es) for f, es in strong.items()], advice=advice)
    medium = {f: es for f, es in multi.items() if max(e.signal or 0 for e in es) >= 50}
    if medium:
        return CheckResult(**base, status=INFO, summary=msg("diag.modem_wifi.medium_family"),
                           details=[describe(f, es) for f, es in medium.items()], advice=advice)
    loud = [e for e in foreign if (e.signal or 0) >= 70]
    if loud:
        return CheckResult(**base, status=INFO, summary=msg("diag.modem_wifi.loud"),
                           details=[msg("diag.modem_wifi.loud_network", ssid=_ssid_label(e.ssid), band=e.band,
                                        channel=e.channel, signal=e.signal, bssid=e.bssid) for e in loud],
                           advice=msg("diag.modem_wifi.advice_loud"))
    return CheckResult(**base, status=OK, summary=msg("diag.modem_wifi.ok"))


# --- 13. physical link (antenna placement) ---------------------------------------------------

LINK_MIN_MINUTES = 30
LINK_WARN_FRACTION = 0.20
LINK_INFO_FRACTION = 0.05


RECENT_WINDOW_S = 3 * 3600
RECENT_MIN_MINUTES = 15


def is_link_bad(m: Minute) -> bool:
    return m.rx_mbps <= 30 and m.router_loss_pct >= 5


def evaluate_link(minutes: list[Minute], now: float | None = None) -> CheckResult:
    base = dict(id=13, key="physical_link", title=title_of("physical_link"))
    if len(minutes) < LINK_MIN_MINUTES:
        return CheckResult(**base, status=INFO,
                           summary=msg("diag.physical_link.not_enough", count=len(minutes), needed=LINK_MIN_MINUTES))
    bad = [m for m in minutes if is_link_bad(m)]
    frac = len(bad) / len(minutes)
    rssi_med = statistics.median(m.rssi for m in minutes)
    details: list[Message] = [msg("diag.physical_link.bad_minutes", bad=len(bad), total=len(minutes), fraction=frac),
                              msg("diag.physical_link.rssi", rssi=float(rssi_med))]
    if frac < LINK_INFO_FRACTION:
        return CheckResult(**base, status=OK, summary=msg("diag.physical_link.ok"), details=details)
    if now is not None:
        recent = [m for m in minutes if m.ts >= now - RECENT_WINDOW_S]
        if len(recent) >= RECENT_MIN_MINUTES:
            recent_frac = sum(is_link_bad(m) for m in recent) / len(recent)
            details.append(msg("diag.physical_link.recent", fraction=recent_frac, total=len(recent)))
            if recent_frac < LINK_INFO_FRACTION:
                return CheckResult(**base, status=INFO, summary=msg("diag.physical_link.improved"),
                                   details=details, advice=msg("diag.physical_link.advice_improved"))
    if rssi_med < -76:
        details.append(msg("diag.physical_link.weak_signal"))
    status = WARN if frac >= LINK_WARN_FRACTION else INFO
    summary = msg("diag.physical_link.warn" if status == WARN else "diag.physical_link.info")
    return CheckResult(**base, status=status, summary=summary, details=details, advice=msg("diag.physical_link.advice"))


# --- 14. bufferbloat (on demand: generates real traffic) --------------------------------------------

BLOAT_WARN_MS, BLOAT_BAD_MS = 30, 100
BLOAT_BAD_LOSS_PCT = 20
BLOAT_MIN_MBPS = 1.0          # below this the "load" did not really load the line
BLOAT_MIN_SAMPLES = 8


def _pctl95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[18] if len(values) >= 20 else max(values)


def _bloat_status(delta_ms: float, loss_pct: float, replies: int) -> str:
    if replies == 0 or loss_pct >= BLOAT_BAD_LOSS_PCT:
        return BAD
    return BAD if delta_ms > BLOAT_BAD_MS else WARN if delta_ms >= BLOAT_WARN_MS else OK


def evaluate_bufferbloat(m: "Any") -> CheckResult:
    """m: bufferbloat.Measurement (idle / download / upload phases of RTT samples)."""
    base = dict(id=14, key="bufferbloat", title=title_of("bufferbloat"))
    idle_med: dict[str, float] = {}
    for label, samples in m.idle.rtts.items():
        got = [s for s in samples if s is not None]
        if len(got) >= BLOAT_MIN_SAMPLES // 2:
            idle_med[label] = statistics.median(got)
    if "internet" not in idle_med:
        return CheckResult(**base, status=INFO, summary=msg("diag.bufferbloat.no_idle"),
                           advice=msg("diag.bufferbloat.advice_retry"))
    details: list[Message] = [msg("diag.bufferbloat.idle", values=[f"{l} {v:.0f} ms" for l, v in idle_med.items()])]
    statuses, delta_by, loss_by = [], {}, {}
    notes: list[Message] = []
    for phase, direction in ((m.download, "download"), (m.upload, "upload")):
        name = msg(f"diag.bufferbloat.{direction}")
        if phase.error and not phase.mbps:
            notes.append(msg("diag.bufferbloat.no_load", phase=name, error=phase.error))
            continue
        if (phase.mbps or 0) < BLOAT_MIN_MBPS:
            notes.append(msg("diag.bufferbloat.weak_load", phase=name, mbps=float(phase.mbps or 0)))
            continue
        for label, samples in phase.rtts.items():
            if label not in idle_med or len(samples) < BLOAT_MIN_SAMPLES:
                continue
            got = [s for s in samples if s is not None]
            loss = 100.0 * (len(samples) - len(got)) / len(samples)
            if not got:
                delta, line = float("inf"), msg("diag.bufferbloat.all_lost", label=label, phase=name)
            else:
                delta = statistics.median(got) - idle_med[label]
                line = msg("diag.bufferbloat.line", label=label, phase=name, mbps=float(phase.mbps),
                           median=float(statistics.median(got)), p95=float(_pctl95(got)), delta=float(delta),
                           loss=loss)
            details.append(line)
            statuses.append(_bloat_status(delta, loss, len(got)))
            delta_by[(label, direction)] = delta
            loss_by[(label, direction)] = loss
    details += notes
    if not statuses:
        return CheckResult(**base, status=INFO, summary=msg("diag.bufferbloat.no_result"), details=details,
                           advice=msg("diag.bufferbloat.advice_unreachable"))
    status = worst(statuses)
    inet = [d for (label, _), d in delta_by.items() if label == "internet"]
    router = [d for (label, _), d in delta_by.items() if label == "router"]
    biggest = max(inet) if inet else 0.0
    worst_loss = max((l for (label, _), l in loss_by.items() if label == "internet"), default=0.0)
    delay_is_the_problem = biggest >= BLOAT_WARN_MS
    if status == OK:
        summary = msg("diag.bufferbloat.ok", delta=float(max(biggest, 0)))
    elif not delay_is_the_problem:
        # Median latency is fine; what fails is packet loss under load (a full queue dropping pings).
        summary = msg("diag.bufferbloat.loss_only", loss=float(worst_loss))
    else:
        where: Message = ""
        if router and max(router) >= BLOAT_WARN_MS:
            where = msg("diag.bufferbloat.where_local")
        elif biggest >= BLOAT_WARN_MS:
            where = msg("diag.bufferbloat.where_wan")
        amount = (msg("diag.bufferbloat.unbounded") if biggest == float("inf")
                  else msg("diag.bufferbloat.by", delta=float(biggest)))
        summary = msg("diag.bufferbloat.rise", amount=amount, where=where)
    advice = msg("diag.bufferbloat.advice") if status != OK else ""
    return CheckResult(**base, status=status, summary=summary, details=details, advice=advice)


# --- 15. path MTU ---------------------------------------------------------------------------------

def evaluate_path_mtu(measured: "Any", current: int | None) -> CheckResult:
    """measured: pathmtu.PathMtu. current: IPv4 MTU of the interface in use."""
    base = dict(id=15, key="path_mtu", title=title_of("path_mtu"))
    details: list[Message] = [msg("diag.path_mtu.target", target=t, mtu=v if v is not None else "—")
                              for t, v in measured.per_target.items()]
    if measured.mtu is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.path_mtu.no_reply"), details=details)
    if current is None:
        return CheckResult(**base, status=INFO, summary=msg("diag.path_mtu.measured", mtu=measured.mtu),
                           details=details)
    if measured.mtu < current:
        return CheckResult(**base, status=WARN, summary=msg("diag.path_mtu.too_big", mtu=measured.mtu, current=current),
                           details=details, advice=msg("diag.path_mtu.advice"), tweak="mtu_path")
    if current < 1500:
        # Windows refuses DF packets above the interface MTU, so nothing above it could be seen.
        return CheckResult(**base, status=OK, summary=msg("diag.path_mtu.ok_lowered", current=current), details=details)
    return CheckResult(**base, status=OK, summary=msg("diag.path_mtu.ok", mtu=measured.mtu), details=details)


def path_mtu_calibrates(measured: "Any", current: int | None) -> bool:
    """Whether the probe saw the real limit (ADR-0015 rule 3): it is capped by the interface's own
    MTU, so a result equal to an already lowered MTU says nothing about the line."""
    return measured.mtu is not None and current is not None and (measured.mtu < current or current >= 1500)



# --- context: lazy, cached data access ----------------------------------------------------------

class Context:
    """Data providers for one diagnostics run. `loaders` override the real ones (tests)."""

    def __init__(self, storage: Storage | None = None, now: float | None = None,
                 loaders: dict[str, Callable[[], Any]] | None = None) -> None:
        self.storage = storage
        self.now = time.time() if now is None else now
        self._cache: dict[str, tuple[bool, Any]] = {}
        self._loaders: dict[str, Callable[[], Any]] = {
            "wifi": winutil.get_wifi_state,
            "adapters": lambda: winutil.get_adapters(physical_only=False),
            "scan": winutil.get_scan,
            "events": lambda: winutil.get_diag_events(7),
            "driver_store": winutil.get_driver_store,
            "uplink": self._load_uplink,
            "dns_servers": self._load_dns_servers,
            "gateway": winutil.get_gateway,
            "ping_rows_1h": lambda: self._ping_rows(3600),
            "ping_rows_5m": lambda: self._ping_rows(300),
            "network_changes": self._load_network_changes,
            "minutes": self._load_minutes,
            "dns_bench": self._load_dns_bench,
            "tweak_states": self._load_tweak_states,
            "bufferbloat": self._load_bufferbloat,
            "path_mtu": self._load_path_mtu,
            "interface_mtu": self._load_interface_mtu,
            "calibrate": lambda: self._calibrate,
            "upload_limit_active": self._load_upload_limit_active,
        }
        self._loaders.update(loaders or {})

    def get(self, name: str) -> Any:
        """Memoised; a failing loader fails identically for every check that needs it."""
        if name not in self._cache:
            try:
                self._cache[name] = (True, self._loaders[name]())
            except Exception as exc:
                self._cache[name] = (False, exc)
        ok, value = self._cache[name]
        if not ok:
            raise value
        return value

    # -- default loaders -----------------------------------------------------------------
    def _load_uplink(self) -> dict | None:
        return winutil.default_route_native() or winutil.get_uplink()

    def _load_dns_servers(self) -> list[str]:
        uplink = self.get("uplink")
        if not uplink:
            return []
        seen: list[str] = []
        for s in winutil.get_dns_servers(uplink["interface_index"]):
            if dnsprobe.is_usable_server(s) and s not in seen:
                seen.append(s)
        return seen

    def _ping_rows(self, seconds: int) -> list[dict]:
        if self.storage is None:
            return []
        return self.storage.query_minute_stats(int(self.now) - seconds, int(self.now) + 60)

    def _load_network_changes(self) -> list[int]:
        """Times of the monitor's network-change events that can touch the last hour's minutes."""
        if self.storage is None:
            return []
        since = int(self.now) - 3600 - 60 * CHANGE_MINUTES
        events = self.storage.query_events(since, int(self.now) + 60, kinds=NETWORK_CHANGE_EVENTS, limit=1000)
        return [e["ts"] for e in events]

    def _load_minutes(self) -> list[Minute]:
        if self.storage is None:
            return []
        start, end = int(self.now) - HISTORY_HOURS * 3600, int(self.now) + 60
        return join_minutes(self.storage.query_wifi_stats(start, end), self.storage.query_minute_stats(start, end))

    def _load_dns_bench(self) -> tuple[list[dnsprobe.ServerBenchmark], list[str], dict[str, str]]:
        in_use = list(self.get("dns_servers"))
        labels = {s: "in_use" for s in in_use}
        gateway = self.get("gateway")
        servers = list(in_use)
        for extra, role in [(gateway, "router")] + [(p, "public") for p in PUBLIC_DNS]:
            if extra and extra not in servers and dnsprobe.is_usable_server(extra):
                servers.append(extra)
                labels[extra] = role
        if gateway in servers and gateway in in_use:
            labels[gateway] = "in_use_router"
        with ThreadPoolExecutor(max_workers=max(1, len(servers))) as pool:
            bench = list(pool.map(lambda s: dnsprobe.benchmark([s], timeout=1.5)[0], servers))
        return bench, in_use, labels

    def _load_bufferbloat(self) -> Any:
        from . import bufferbloat
        targets = {"internet": "1.1.1.1"}
        gateway = self.get("gateway")
        if gateway:
            targets = {"router": gateway, **targets}
        down, up = bufferbloat.real_loads()
        return bufferbloat.measure(bufferbloat.real_ping(), targets, down, up)

    def _load_path_mtu(self) -> Any:
        from . import pathmtu
        return pathmtu.measure(pathmtu.icmp_ping())

    def _load_interface_mtu(self) -> int | None:
        from .winsys import WindowsSystem
        uplink = self.get("uplink")
        return WindowsSystem().interface_mtu_get(uplink["interface_index"]) if uplink else None

    def _load_upload_limit_active(self) -> bool:
        from .tweaks import UploadLimitTweak
        from .winsys import WindowsSystem
        return WindowsSystem().qos_throttle_get(UploadLimitTweak.POLICY) is not None

    def _calibrate(self, kind: str, value: float, detail: dict[str, Any], tweak_active: bool) -> bool:
        """Store a measurement for the measured tweaks (ADR-0015), tied to the network in use. Only a
        run with storage (the app, the CLI) records: a bare Context is a test or a one-off probe."""
        if self.storage is None:
            return False
        from . import calibration
        from .winsys import WindowsSystem
        net = WindowsSystem().current_network()
        return calibration.record(kind, value, net["key"] if net else None, self.now, detail=detail,
                                  tweak_active=tweak_active)

    def _load_tweak_states(self) -> dict[str, dict] | None:
        try:
            from . import tweaks
        except ImportError:
            return None
        lister = getattr(tweaks, "list_states", None)
        return lister() if callable(lister) else None


def _events_error(events: dict, label: str) -> str | None:
    msgs = [e for e in events.get("errors", []) if e.startswith(label)]
    return "; ".join(msgs) or None


# --- the checks ---------------------------------------------------------------------------------

def check_driver(ctx: Context) -> CheckResult:
    ev = ctx.get("events")
    return evaluate_driver(_wifi_adapter(ctx.get("adapters"), _connected_interface(ctx)), ev.get("ihv_stops", []),
                           ctx.get("driver_store"), ctx.now, _events_error(ev, "ihv_stops"))


def check_signal(ctx: Context) -> CheckResult:
    return evaluate_signal(ctx.get("wifi"))


def check_interference(ctx: Context) -> CheckResult:
    return evaluate_interference(ctx.get("wifi"), ctx.get("scan"))


def check_drops(ctx: Context) -> CheckResult:
    ev = ctx.get("events")
    err = _events_error(ev, "disconnects") or _events_error(ev, "limited_connectivity")
    return evaluate_drops(ev.get("disconnects", []), ev.get("limited_connectivity", []), err, ctx.now)


def check_ping(ctx: Context) -> CheckResult:
    return evaluate_ping(ctx.get("ping_rows_1h"), ctx.get("ping_rows_5m"), changes=ctx.get("network_changes"))


def check_dns(ctx: Context) -> CheckResult:
    bench, in_use, labels = ctx.get("dns_bench")
    return evaluate_dns(bench, in_use, labels)


def check_tcp(ctx: Context) -> CheckResult:
    ev = ctx.get("events")
    return evaluate_tcp(ev.get("port_exhaustion", []), ev.get("time_wait"), _events_error(ev, "port_exhaustion"))


def check_vpn(ctx: Context) -> CheckResult:
    return evaluate_vpn(ctx.get("adapters"))


def check_wired(ctx: Context) -> CheckResult:
    return evaluate_wired(ctx.get("adapters"), ctx.get("wifi"))


def check_tweaks(ctx: Context) -> CheckResult:
    return evaluate_tweaks(ctx.get("tweak_states"))


def check_mlo(ctx: Context) -> CheckResult:
    return evaluate_mlo(ctx.get("wifi"), _wifi_adapter(ctx.get("adapters"), _connected_interface(ctx)),
                        ctx.get("scan"), ctx.get("minutes"), ctx.now)


def check_modem_wifi(ctx: Context) -> CheckResult:
    return evaluate_modem_wifi(ctx.get("wifi"), ctx.get("scan"))


def check_link(ctx: Context) -> CheckResult:
    return evaluate_link(ctx.get("minutes"), ctx.now)


def check_path_mtu(ctx: Context) -> CheckResult:
    measured, current = ctx.get("path_mtu"), ctx.get("interface_mtu")
    if path_mtu_calibrates(measured, current):
        _record(ctx, "path_mtu", measured.mtu, {"per_target": measured.per_target}, lambda: False)
    return evaluate_path_mtu(measured, current)


# (id, key, function). The title of each is the message "diag.<key>.title".
CHECKS: list[tuple[int, str, Callable[[Context], CheckResult]]] = [
    (1, "driver", check_driver),
    (2, "signal", check_signal),
    (3, "interference", check_interference),
    (4, "drops", check_drops),
    (5, "ping", check_ping),
    (6, "dns", check_dns),
    (7, "tcp_ports", check_tcp),
    (8, "vpn", check_vpn),
    (9, "wired", check_wired),
    (10, "tweaks", check_tweaks),
    (11, "wifi7_mlo", check_mlo),
    (12, "modem_wifi", check_modem_wifi),
    (13, "physical_link", check_link),
    (15, "path_mtu", check_path_mtu),
]


def check_bufferbloat(ctx: Context) -> CheckResult:
    m = ctx.get("bufferbloat")
    result = evaluate_bufferbloat(m)
    up = m.upload
    if up.mbps and up.mbps >= BLOAT_MIN_MBPS and not up.error:
        _record(ctx, "upload_mbps", float(up.mbps), {"download_mbps": m.download.mbps},
                lambda: bool(ctx.get("upload_limit_active")))
    if result.status in (WARN, BAD) and _upload_is_worse(result):
        result = replace(result, tweak="upload_shaping")
    return result


def _upload_is_worse(result: CheckResult) -> bool:
    """The upload phase is (one of) the worst: the only direction a PC-side limit can help."""
    for d in result.details:
        if not isinstance(d, dict) or d.get("key") not in ("diag.bufferbloat.line", "diag.bufferbloat.all_lost"):
            continue
        params = d.get("params", {})
        if params.get("phase", {}).get("key") == "diag.bufferbloat.upload" and                 params.get("delta", float("inf")) >= BLOAT_WARN_MS:
            return True
    return False


def _record(ctx: Context, kind: str, value: float, detail: dict[str, Any], active: Callable[[], bool]) -> None:
    """A failure to store a calibration must not change the check's result."""
    try:
        ctx.get("calibrate")(kind, value, detail, active())
    except Exception:
        pass


# Checks that generate real traffic: never part of the default run, only when asked for by number.
ON_DEMAND_CHECKS: list[tuple[int, str, Callable[[Context], CheckResult]]] = [
    (14, "bufferbloat", check_bufferbloat),
]


@dataclass
class Report:
    ts: int
    results: list[CheckResult]

    @property
    def worst(self) -> str:
        return worst([r.status for r in self.results])


def run_all(ctx: Context, only: set[int] | None = None) -> Report:
    results = []
    # On-demand checks join the run only when named in `only`.
    wanted = CHECKS + [c for c in ON_DEMAND_CHECKS if only is not None and c[0] in only]
    for cid, key, fn in wanted:
        if only is not None and cid not in only:
            continue
        try:
            results.append(fn(ctx))
        except Exception as exc:  # one broken check must not hide the others
            results.append(CheckResult(cid, key, title_of(key), INFO, msg("diag.not_run"),
                                       details=[f"{type(exc).__name__}: {exc}"], error=f"{type(exc).__name__}: {exc}"))
    return Report(int(ctx.now), results)


def save_report(storage: Storage, report: Report) -> int:
    return storage.save_diagnostic_run(report.ts, report.worst, [r.to_dict() for r in report.results])


def compare_runs(old: list[dict], new: list[dict]) -> list[dict[str, Any]]:
    """Per-check status changes between two saved runs (only the checks that changed)."""
    old_by, new_by = {r["key"]: r for r in old}, {r["key"]: r for r in new}
    changes = []
    for key in list(new_by) + [k for k in old_by if k not in new_by]:
        o, n = old_by.get(key), new_by.get(key)
        o_status, n_status = (o or {}).get("status"), (n or {}).get("status")
        if o_status == n_status:
            continue
        if o is None or n is None:
            change = "new" if o is None else "removed"
        else:
            ro, rn = _BETTER_RANK[o_status], _BETTER_RANK[n_status]
            change = "better" if rn < ro else "worse" if rn > ro else "changed"
        changes.append({"key": key, "title": (n or o)["title"], "old": o_status, "new": n_status, "change": change})
    return changes


# --- CLI -------------------------------------------------------------------------------------

_ICON = {OK: "OK  ", INFO: "INFO", WARN: "WARN", BAD: "BAD "}


def format_report(report: Report, lang: str | None = None) -> str:
    lines = [i18n.t("diag.cli.header", lang, time=_fmt_ts(report.ts), status=report.worst.upper()), ""]
    for r in (r.localized(lang) for r in report.results):
        lines.append(f"[{_ICON[r.status]}] {r.id:>2}. {r.title}: {r.summary}")
        lines += [f"        {d}" for d in r.details]
        if r.advice:
            lines.append(f"        → {r.advice}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the read-only network diagnostics.")
    ap.add_argument("--only", help="comma-separated check numbers, e.g. 2,13")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--no-save", action="store_true", help="do not store this run in metrics.db")
    ap.add_argument("--compare", action="store_true", help="show changes against the previous saved run")
    ap.add_argument("--bufferbloat", action="store_true",
                    help="run ONLY the bufferbloat check (~30 s, generates up to ~200 MB of traffic)")
    ap.add_argument("--lang", help="language for the output (default: settings.json ui.language)")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    only = {int(x) for x in args.only.split(",")} if args.only else None
    if args.bufferbloat:
        only = {14}
        args.no_save = True   # a one-check run would replace the full run that --compare diffs against
    with Storage(config.db_path()) as storage:
        previous = storage.latest_diagnostic_run() if args.compare else None
        report = run_all(Context(storage), only)
        run_id = None if args.no_save else save_report(storage, report)
    changes = compare_runs(previous["results"], [r.to_dict() for r in report.results]) if previous else None
    lang = args.lang

    if args.json:
        print(json.dumps(i18n.localize({"run_id": run_id, "ts": report.ts, "worst": report.worst,
                                        "results": [r.to_dict() for r in report.results], "changes": changes}, lang),
                         ensure_ascii=False, indent=2))
    else:
        print(format_report(report, lang))
        if run_id is not None:
            print("\n" + i18n.t("diag.cli.saved", lang, run_id=run_id))
        if changes is not None:
            print("\n" + i18n.t("diag.cli.compared", lang, time=_fmt_ts(previous["ts"])))
            print("  " + i18n.t("diag.cli.no_changes", lang) if not changes else "\n".join(
                f"  {i18n.render(c['title'], lang)}: {c['old']} → {c['new']} ({c['change']})" for c in changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
