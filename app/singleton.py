"""Single-instance guard using a named Windows mutex.

Unlike a PID file it cannot go stale: the OS drops the mutex when the owning
process dies, so a PID reused after a reboot can never block a start.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183

_kernel32 = None


def _k32():
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateMutexW.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        _kernel32 = k
    return _kernel32


class SingleInstance:
    """`Local\\` names are per logon session, which is what a per-user tool wants."""

    def __init__(self, name: str) -> None:
        self.name = name if name.startswith(("Local\\", "Global\\")) else f"Local\\{name}"
        self._handle = None

    def acquire(self) -> bool:
        """True if this process is now the only instance. Calling it again is a no-op."""
        if self._handle is not None:
            return True
        k = _k32()
        ctypes.set_last_error(0)
        handle = k.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not handle:
            # ACCESS_DENIED: the mutex exists but was created by a process with a different
            # integrity level (e.g. an elevated copy). Someone else owns it.
            if err == ERROR_ACCESS_DENIED:
                return False
            raise OSError(err, f"CreateMutexW({self.name!r}) failed")
        if err == ERROR_ALREADY_EXISTS:
            k.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            _k32().CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SingleInstance":
        return self

    def __exit__(self, *exc) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
