"""Windows toast notifications for outages and watchdog actions.

Toasts go through PowerShell's WinRT access (no third-party package, ADR-0001). The strings
travel as base64 and are put into the toast with CreateTextNode, so quotes, '<' or '&' in a
message cannot break the script or the XML. The toast is attributed to Windows PowerShell's
own AppUserModelID because a plain script has none of its own.

    OutageNotifier  pure state machine: which toast does this outage state call for?
    Notifier        rate limiting + background delivery
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from .i18n import format_duration, t
from .winsys import ps_literal
from .winutil import PowerShellError, run_powershell

log = logging.getLogger("stableinternet.notify")

AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text/><text/></binding></visual></toast>")
$texts = $xml.GetElementsByTagName('text')
[void]$texts.Item(0).AppendChild($xml.CreateTextNode({title}))
[void]$texts.Item(1).AppendChild($xml.CreateTextNode({message}))
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}')
if ('{show}' -eq 'yes') { $notifier.Show($toast) }
$notifier.Setting
"""


def send_toast(title: str, message: str, *, show: bool = True,
               ps: Callable[..., str] = run_powershell) -> str:
    """Show one toast; returns the notifier setting ("Enabled", "DisabledForUser"...).
    show=False builds everything without displaying (for tests). Raises PowerShellError."""
    script = (_TOAST_PS.replace("{title}", ps_literal(title)).replace("{message}", ps_literal(message))
              .replace("{aumid}", AUMID).replace("{show}", "yes" if show else "no"))
    lines = ps(script, timeout=30).strip().splitlines()
    return lines[-1] if lines else ""


class Notifier:
    """Rate-limited, non-blocking. Never raises: a missing toast must not hurt the monitor.

    Limits: the same title at most once per `cooldown_s`; at most `max_per_window` toasts per
    `window_s` overall (a flapping network must not bury the screen in notifications)."""

    def __init__(self, *, enabled: Callable[[], bool] = lambda: True, send: Callable[[str, str], str] = send_toast,
                 cooldown_s: float = 30, max_per_window: int = 6, window_s: float = 600,
                 clock: Callable[[], float] = time.time, run_async: bool = True) -> None:
        self._enabled, self._send, self._clock, self._async = enabled, send, clock, run_async
        self.cooldown_s, self.max_per_window, self.window_s = cooldown_s, max_per_window, window_s
        self._lock = threading.Lock()
        self._recent: deque[float] = deque()
        self._last_by_title: dict[str, float] = {}
        self._warned: set[str] = set()
        self.sent = 0
        self.dropped = 0

    def notify(self, title: str, message: str) -> bool:
        """True if the toast was accepted for delivery."""
        try:
            if not self._enabled():
                return False
        except Exception:
            return False
        now = self._clock()
        with self._lock:
            while self._recent and self._recent[0] <= now - self.window_s:
                self._recent.popleft()
            last = self._last_by_title.get(title)
            if (last is not None and now - last < self.cooldown_s) or len(self._recent) >= self.max_per_window:
                self.dropped += 1
                return False
            self._recent.append(now)
            self._last_by_title[title] = now
            self.sent += 1
        if self._async:
            threading.Thread(target=self._deliver, args=(title, message), name="toast", daemon=True).start()
        else:
            self._deliver(title, message)
        return True

    def _deliver(self, title: str, message: str) -> None:
        try:
            self._send(title, message)
        except (PowerShellError, OSError, ValueError) as exc:
            if str(exc) not in self._warned:   # one log line per distinct failure
                self._warned.add(str(exc))
                log.warning("toast not shown: %s", exc)
        except Exception:
            log.exception("toast failed")


KNOWN_OUTAGES = ("router", "internet")


def _outage_title(kind: str, lang: str | None) -> str:
    if kind in KNOWN_OUTAGES:
        return t(f"notify.outage.{kind}.title", lang)
    return t("notify.outage.other.title", lang, kind=kind)


def _outage_body(kind: str, duration: str, lang: str | None) -> str:
    key = f"notify.outage.{kind}.body" if kind in KNOWN_OUTAGES else "notify.outage.other.body"
    return t(key, lang, duration=duration)


class OutageNotifier:
    """Turns the monitor's open-outage state into toasts: one when an outage has lasted
    `after_s` (short blips are not worth interrupting anyone), one when it is over - but
    only for outages that got the first toast. Text is in the language chosen at send time
    (`lang` pins one, for tests)."""

    def __init__(self, notify: Callable[[str, str], Any], after_s: float = 30, lang: str | None = None) -> None:
        self._notify, self.after_s, self.lang = notify, after_s, lang
        self._announced: dict[str, float] = {}     # kind -> outage start

    def update(self, now: float, active: dict[str, float]) -> None:
        for kind, start in active.items():
            if self._announced.get(kind) != start and now - start >= self.after_s:
                self._announced[kind] = start
                duration = format_duration(now - start, self.lang)
                self._notify(_outage_title(kind, self.lang), _outage_body(kind, duration, self.lang))
        for kind in [k for k in self._announced if k not in active]:
            start = self._announced.pop(kind)
            self._notify(t("notify.back.title", self.lang),
                         t("notify.back.body", self.lang, what=_outage_title(kind, self.lang),
                           duration=format_duration(now - start, self.lang)))

    def reset(self) -> None:
        """After a monitoring gap (sleep/hang): forget silently. Whatever happened while we were
        not looking is unknown, so announcing "recovered" would be a guess."""
        self._announced.clear()


# Watchdog events that deserve a toast -> translation key of the title.
# dry_run, skip and recovered stay quiet.
WATCHDOG_TOASTS = {"watchdog_action": "notify.watchdog.action.title",
                   "watchdog_tripped": "notify.watchdog.tripped.title",
                   "watchdog_error": "notify.watchdog.error.title"}
