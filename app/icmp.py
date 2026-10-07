"""ICMP echo via IcmpSendEcho (iphlpapi). No Admin needed, no child process.

IPv4 only. A Pinger owns one ICMP handle and is not thread-safe: use one per
thread (the monitor runs one thread per target).
"""
from __future__ import annotations

import ctypes
import socket
import time
from ctypes import wintypes
from dataclasses import dataclass

IP_SUCCESS = 0
IP_PACKET_TOO_BIG = 11009
IP_REQ_TIMED_OUT = 11010
IP_FLAG_DF = 0x02   # IP_OPTION_INFORMATION.Flags: do not fragment

_STATUS_TEXT = {
    11002: "network unreachable",
    11003: "host unreachable",
    11004: "protocol unreachable",
    11005: "port unreachable",
    11009: "packet too big",
    11010: "timed out",
    11013: "TTL expired in transit",
    11050: "general failure",
}

_PAYLOAD = b"StableInternet"
_REPLY_BUFFER = 256  # >= sizeof(ICMP_ECHO_REPLY) + payload + 8 bytes of ICMP error data
_REPLY_SLACK = 64    # room for the reply header and ICMP error data on top of a sized payload


class _IpOptionInformation(ctypes.Structure):
    _fields_ = [
        ("Ttl", ctypes.c_ubyte),
        ("Tos", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
        ("OptionsSize", ctypes.c_ubyte),
        ("OptionsData", ctypes.c_void_p),
    ]


class _IcmpEchoReply(ctypes.Structure):
    _fields_ = [
        ("Address", wintypes.ULONG),
        ("Status", wintypes.ULONG),
        ("RoundTripTime", wintypes.ULONG),
        ("DataSize", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("Data", ctypes.c_void_p),
        ("Options", _IpOptionInformation),
    ]


@dataclass(frozen=True)
class PingResult:
    ok: bool
    rtt_ms: float | None
    status: int          # IP_STATUS from Windows; -1 = failed before sending
    error: str = ""


_iphlpapi = None


def _lib():
    global _iphlpapi
    if _iphlpapi is None:
        lib = ctypes.WinDLL("iphlpapi", use_last_error=True)
        lib.IcmpCreateFile.restype = wintypes.HANDLE
        lib.IcmpCloseHandle.argtypes = [wintypes.HANDLE]
        lib.IcmpSendEcho.argtypes = [
            wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.WORD,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ]
        lib.IcmpSendEcho.restype = wintypes.DWORD
        _iphlpapi = lib
    return _iphlpapi


def _ipv4_to_ulong(address: str) -> int:
    """IN_ADDR as the ULONG Windows expects (bytes in network order, in memory)."""
    return int.from_bytes(socket.inet_aton(address), "little")


def status_text(status: int) -> str:
    return _STATUS_TEXT.get(status, f"IP_STATUS {status}")


class Pinger:
    def __init__(self) -> None:
        self._lib = _lib()
        self._handle = self._lib.IcmpCreateFile()
        if self._handle in (None, wintypes.HANDLE(-1).value):
            raise OSError(ctypes.get_last_error(), "IcmpCreateFile failed")
        self._buf = ctypes.create_string_buffer(_REPLY_BUFFER)

    def ping(self, address: str, timeout_ms: int = 900, size: int | None = None,
             dont_fragment: bool = False) -> PingResult:
        """One echo. Never raises for network failures; they come back as ok=False.

        `size` is the ICMP payload in bytes (the IP packet is 28 bytes larger). With
        `dont_fragment` a packet larger than the path allows fails with IP_PACKET_TOO_BIG
        (when a router or the local stack says so) or times out (when it is dropped silently).
        """
        try:
            dest = _ipv4_to_ulong(address)
        except (OSError, ValueError, TypeError):
            return PingResult(False, None, -1, f"invalid IPv4 address: {address!r}")
        if self._handle is None:
            return PingResult(False, None, -1, "pinger closed")

        payload = _PAYLOAD if size is None else (_PAYLOAD * (size // len(_PAYLOAD) + 1))[:max(0, size)]
        buf = self._buf
        if len(payload) + _REPLY_SLACK > _REPLY_BUFFER:
            buf = ctypes.create_string_buffer(ctypes.sizeof(_IcmpEchoReply) + len(payload) + _REPLY_SLACK)
        options = ctypes.byref(_IpOptionInformation(Ttl=128, Flags=IP_FLAG_DF)) if dont_fragment else None

        started = time.perf_counter()
        replies = self._lib.IcmpSendEcho(
            self._handle, dest, payload, len(payload), options,
            buf, len(buf), timeout_ms,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if replies == 0:
            err = ctypes.get_last_error()
            return PingResult(False, None, err, status_text(err))

        reply = _IcmpEchoReply.from_buffer(buf)
        if reply.Status != IP_SUCCESS:
            return PingResult(False, None, reply.Status, status_text(reply.Status))
        # RoundTripTime has 1 ms resolution; fall back to wall clock when it reads 0
        # so sub-millisecond LAN replies don't all look identical.
        rtt = float(reply.RoundTripTime) or round(min(elapsed_ms, 1.0), 3)
        return PingResult(True, rtt, IP_SUCCESS)

    def close(self) -> None:
        if self._handle is not None:
            self._lib.IcmpCloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "Pinger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
