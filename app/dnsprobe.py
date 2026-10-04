"""DNS probe over raw UDP: builds the query itself so Windows' resolver cache and
search-list logic never get involved. Used to benchmark individual DNS servers.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import statistics
import struct
import time
from dataclasses import dataclass, field

QTYPE_A = 1
RCODE_NAMES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}

DEFAULT_NAMES = ("google.com", "cloudflare.com", "microsoft.com", "youtube.com", "facebook.com")


@dataclass(frozen=True)
class DnsResult:
    ok: bool                # got a well-formed answer with NOERROR (NXDOMAIN counts as a reply, not ok)
    rtt_ms: float | None    # set whenever a valid reply arrived, even with an error rcode
    rcode: int | None
    answers: int
    error: str = ""


class DnsError(ValueError):
    pass


def build_query(name: str, qid: int | None = None, qtype: int = QTYPE_A) -> tuple[int, bytes]:
    """Return (id, packet) for a recursive query of `name`."""
    if qid is None:
        qid = int.from_bytes(os.urandom(2), "big")
    labels = [lab for lab in name.rstrip(".").split(".") if lab]
    if not labels:
        raise DnsError("empty name")
    qname = b""
    for lab in labels:
        try:
            raw = lab.encode("idna")
        except UnicodeError as exc:  # label too long / empty after IDNA, etc.
            raise DnsError(f"bad label in {name!r}: {exc}") from exc
        if not 0 < len(raw) <= 63:
            raise DnsError(f"bad label length in {name!r}")
        qname += bytes([len(raw)]) + raw
    qname += b"\x00"
    header = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)  # RD set, one question
    return qid, header + qname + struct.pack("!HH", qtype, 1)


def parse_response(data: bytes, qid: int) -> tuple[int, int]:
    """Validate a reply and return (rcode, answer_count). Raises DnsError if it
    is not a response to query `qid`. Records themselves are not decoded."""
    if len(data) < 12:
        raise DnsError("short packet")
    rid, flags, _qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    if rid != qid:
        raise DnsError("id mismatch")
    if not flags & 0x8000:
        raise DnsError("not a response")
    return flags & 0x000F, an


def is_usable_server(address: str) -> bool:
    """False for unparseable addresses and IPv6 link-local (needs a scope id)."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if "%" in address:
        return False
    return not (ip.version == 6 and ip.is_link_local)


def probe(server: str, name: str = "google.com", timeout: float = 2.0, port: int = 53) -> DnsResult:
    """One query to one server. Never raises for network failures."""
    if not is_usable_server(server):
        return DnsResult(False, None, None, 0, f"unusable server address {server!r}")
    try:
        qid, packet = build_query(name)
    except DnsError as exc:
        return DnsResult(False, None, None, 0, str(exc))
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.connect((server, port))  # connected UDP drops datagrams from other sources
            started = time.perf_counter()
            sock.send(packet)
            deadline = started + timeout
            while True:
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    return DnsResult(False, None, None, 0, "timeout")
                rtt = (time.perf_counter() - started) * 1000.0
                try:
                    rcode, answers = parse_response(data, qid)
                except DnsError:
                    # Stray or late packet: keep waiting until the deadline.
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        return DnsResult(False, None, None, 0, "timeout")
                    sock.settimeout(remaining)
                    continue
                err = "" if rcode == 0 else RCODE_NAMES.get(rcode, f"rcode {rcode}")
                return DnsResult(rcode == 0, rtt, rcode, answers, err)
    except OSError as exc:
        return DnsResult(False, None, None, 0, f"{type(exc).__name__}: {exc}")


@dataclass
class ServerBenchmark:
    server: str
    sent: int = 0
    replies: int = 0          # valid replies incl. error rcodes
    failures: int = 0         # timeouts, socket errors, SERVFAIL/REFUSED...
    rtts_ms: list[float] = field(default_factory=list)

    @property
    def avg_ms(self) -> float | None:
        return round(statistics.fmean(self.rtts_ms), 1) if self.rtts_ms else None

    @property
    def median_ms(self) -> float | None:
        return round(statistics.median(self.rtts_ms), 1) if self.rtts_ms else None


def benchmark(servers: list[str], names: tuple[str, ...] = DEFAULT_NAMES,
              timeout: float = 2.0) -> list[ServerBenchmark]:
    """Query every name on every server once. Results keep the input order.

    NXDOMAIN is a normal answer for the timing; other error rcodes and timeouts
    count as failures.
    """
    out: list[ServerBenchmark] = []
    for server in servers:
        bench = ServerBenchmark(server)
        for name in names:
            res = probe(server, name, timeout)
            bench.sent += 1
            if res.rtt_ms is not None:
                bench.replies += 1
            if res.ok or res.rcode == 3:
                bench.rtts_ms.append(res.rtt_ms)
            else:
                bench.failures += 1
        out.append(bench)
    return out
