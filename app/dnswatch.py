"""Notice when the DNS servers of the network in use change (malware, a VPN left behind, a
hijacked router...). Read-only: it reports, it never changes DNS.

    DnsWatch    pure state: which event does this observation call for?

A "network" is (interface index, gateway, SSID). Moving to another network is normal and is
only recorded (dns_observed); the same network with different DNS servers is reported
(dns_changed, plus a toast) once the new value has been seen twice in a row, so a DHCP renew
that briefly shows nothing or a half-written list does not raise an alarm.

A change the app made itself (a tweak that sets DNS, ADR-0015) is no anomaly: when the tweak
manager recorded such a change a few minutes ago, the confirmed change is only recorded as
dns_observed.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .i18n import msg

KINDS = ("dns_observed", "dns_changed")
CONFIRM = 2
# Tweaks that change the DNS servers, and how long after their tweak_enabled / tweak_disabled event
# a DNS change still counts as theirs (longer than two looks of the monitor, dns_watch.interval_s).
DNS_TWEAK_IDS = ("dns_fastest",)
TWEAK_EVENT_KINDS = ("tweak_enabled", "tweak_disabled", "tweak_failed")   # a failed change may still have moved DNS
APP_CHANGE_WINDOW_S = 300


def app_changed(events: Iterable[dict[str, Any]], now: float) -> bool:
    """True if one of the stored events says a DNS tweak changed the machine within the window."""
    for e in events:
        message = e.get("message")
        params = message.get("params", {}) if isinstance(message, dict) else {}
        if (e.get("kind") in TWEAK_EVENT_KINDS and params.get("tweak_id") in DNS_TWEAK_IDS
                and now - APP_CHANGE_WINDOW_S <= e.get("ts", 0) <= now + 60):
            return True
    return False


def network_key(interface_index: int | None, gateway: str | None, ssid: str | None) -> str:
    return f"{interface_index}|{gateway or ''}|{ssid or ''}"


class DnsWatch:
    def __init__(self, confirm: int = CONFIRM) -> None:
        self.confirm = confirm
        self.known: dict[str, list[str]] = {}
        self._pending: tuple[str, tuple[str, ...], int] | None = None

    def seed(self, events: Iterable[dict[str, Any]]) -> None:
        """Rebuild what each network used from stored events (oldest first)."""
        for e in events:
            message = e.get("message")
            params = message.get("params", {}) if isinstance(message, dict) else {}
            servers = params.get("servers") or params.get("new")
            if params.get("network") and isinstance(servers, list):
                self.known[params["network"]] = list(servers)

    def observe(self, network: str, servers: list[str],
                by_app: Callable[[], bool] | None = None) -> list[tuple[str, Any, str]]:
        """Events (kind, message, level) for one look at the DNS servers in use. `by_app` is asked,
        only when a change is confirmed, whether the app itself changed DNS a moment ago."""
        if not servers:
            self._pending = None   # disconnected or mid-renew: nothing to judge
            return []
        known = self.known.get(network)
        if known is None:
            self.known[network] = list(servers)
            self._pending = None
            return [("dns_observed", msg("event.dns_observed", servers=list(servers), network=network), "info")]
        if list(servers) == known:
            self._pending = None
            return []
        key = (network, tuple(servers))
        count = self._pending[2] + 1 if self._pending and self._pending[:2] == key else 1
        self._pending = (*key, count)
        if count < self.confirm:
            return []
        self._pending = None
        self.known[network] = list(servers)
        if by_app is not None and by_app():
            return [("dns_observed", msg("event.dns_app_changed", servers=list(servers), network=network), "info")]
        return [("dns_changed", msg("event.dns_changed", old=list(known), new=list(servers), network=network), "warn")]
