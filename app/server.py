"""Local HTTP API + UI server. Runs inside the monitor process, NOT elevated (ADR-0005).

Every request passes the same gate before any handler runs (docs/SECURITY.md):
  1. Host must be exactly 127.0.0.1:<port> or localhost:<port>      (DNS rebinding)
  2. /api/*: X-Token must match the per-start random token           (CSRF from other sites)
  3. writes: Origin must be ours and Sec-Fetch-Site not cross/same-site
OPTIONS is refused and no CORS header is ever sent.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__, config, i18n, winutil
from .i18n import msg

log = logging.getLogger("stableinternet.server")

MAX_BODY = 64 * 1024
WEB_ROOT = config.ROOT / "web"
STATIC_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2",
                ".json": "application/json; charset=utf-8"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'none'"),
}
TWEAK_ID = re.compile(r"^[a-z0-9_]{1,64}$")
ACTIONS = ("reconnect", "restart_adapter", "flush_dns", "renew_dhcp")

# Settings the API may change, with their accepted type and range.
SETTINGS_SCHEMA: dict[str, dict[str, tuple]] = {
    "watchdog": {"enabled": (bool,), "dry_run": (bool,), "threshold_s": ((int, float), 5, 3600),
                 "cooldown_s": ((int, float), 30, 86400), "max_per_hour": (int, 1, 30),
                 "verify_s": ((int, float), 10, 3600), "trip_after": (int, 1, 20)},
    "notify": {"enabled": (bool,)},   # re-read every 10 s by the running monitor
    "failover": {"enabled": (bool,), "dry_run": (bool,)},   # ADR-0008; re-read every 10 s
    # (str, allowed-values function): the language must have a catalog, or be "auto".
    "ui": {"language": (str, lambda: [entry["code"] for entry in i18n.available()] + [i18n.AUTO])},
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


# --- background jobs --------------------------------------------------------------------

class Jobs:
    """Slow work (diagnostics ~8 s, tweak reads ~11 s, UAC waits) runs off the request thread.
    `single` makes a second submit of the same key return the running job instead of a new one."""

    KEEP = 50

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running_by_key: dict[str, str] = {}
        self._clock = clock

    def submit(self, kind: str, fn: Callable[..., Any], single: str | None = None, sync: bool = False,
               progress: bool = False) -> dict:
        """With `progress`, `fn` is called with one argument: a callable that replaces the job's
        "progress" field (a dict), which GET /api/jobs/<id> returns while the job runs."""
        with self._lock:
            if single and single in self._running_by_key:
                return dict(self._jobs[self._running_by_key[single]])
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "kind": kind, "status": "running", "started": self._clock(), "result": None,
                   "error": None, "progress": None}
            self._jobs[job_id] = job
            if single:
                self._running_by_key[single] = job_id
            for old in list(self._jobs)[:-self.KEEP]:
                if self._jobs[old]["status"] != "running":
                    del self._jobs[old]

        def set_progress(value: dict) -> None:
            with self._lock:
                job["progress"] = dict(value)

        def run() -> None:
            try:
                result, error, status = (fn(set_progress) if progress else fn()), None, "done"
            except Exception as exc:
                log.exception("job %s failed", kind)
                result, error, status = None, f"{type(exc).__name__}: {exc}", "error"
            with self._lock:
                job.update(status=status, result=result, error=error, finished=self._clock())
                if single:
                    self._running_by_key.pop(single, None)

        if sync:
            run()
        else:
            threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
        with self._lock:
            return dict(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


# --- the API -------------------------------------------------------------------------------

class Api:
    """Pure request handlers: (method, path, query, body) -> (status, JSON). All dependencies injectable."""

    TWEAK_CACHE_S = 60

    def __init__(self, *, monitor: Any, storage: Any, failover: Any = None,
                 load_settings: Callable[[], dict] = config.load_settings,
                 save_settings: Callable[[dict], None] = config.save_settings,
                 is_admin: Callable[[], bool] = winutil.is_admin,
                 tweak_manager: Callable[[], Any] | None = None,
                 elevate: Callable[[str, str], Any] | None = None,
                 actions: Any = None,
                 run_diagnostics: Callable[..., dict] | None = None,
                 run_bufferbloat: Callable[[], dict] | None = None,
                 clock: Callable[[], float] = time.time,
                 load_backup: Callable[[], dict] = config.load_backup,
                 autostart_status: Callable[[], Any] | None = None,
                 route: Callable[[], dict | None] = winutil.default_route_native,
                 measure_tweak: Callable[[str], dict | None] | None = None,
                 network_id: Callable[[], str | None] | None = None,
                 sync_jobs: bool = False) -> None:
        self.monitor, self.storage, self.failover = monitor, storage, failover
        self._load, self._save, self._is_admin, self._clock = load_settings, save_settings, is_admin, clock
        self._load_backup = load_backup
        self._route = route
        self._tweak_manager = tweak_manager or self._default_tweak_manager
        self._elevate = elevate or self._default_elevate
        self._actions = actions
        self._run_diagnostics = run_diagnostics or self._default_diagnostics
        self._run_bufferbloat = run_bufferbloat or self._default_bufferbloat
        self._measure_tweak = measure_tweak or self._default_measure_tweak
        self._network_id = network_id or self._default_network_id
        self.jobs = Jobs(clock)
        self._sync = sync_jobs
        self._settings_lock = threading.Lock()
        # Measurements that load the line (check #14, a measured tweak) must never overlap: each would
        # see about half the bandwidth, and a measured tweak would derive half the cap it should.
        self._line_lock = threading.Lock()
        self._tweak_cache: dict[str, Any] = {"states": None, "refreshed_at": None}
        self._last_bufferbloat: list[dict] = []   # on-demand results are not saved as runs
        self._autostart_status = autostart_status or self._default_autostart_status
        self._autostart_cache: tuple[float, dict] | None = None
        self._routes: list[tuple[str, re.Pattern, Callable]] = [
            ("GET", re.compile(r"^/api/state$"), self.state),
            ("GET", re.compile(r"^/api/i18n$"), self.translations),
            ("GET", re.compile(r"^/api/autostart$"), self.autostart),
            ("GET", re.compile(r"^/api/live$"), self.live),
            ("GET", re.compile(r"^/api/history$"), self.history),
            ("GET", re.compile(r"^/api/events$"), self.events),
            ("GET", re.compile(r"^/api/tweaks$"), self.tweaks),
            ("GET", re.compile(r"^/api/impact$"), self.impact),
            ("GET", re.compile(r"^/api/suggestions$"), self.suggestions),
            ("GET", re.compile(r"^/api/value$"), self.value),
            ("GET", re.compile(r"^/api/failover$"), self.failover_state),
            ("POST", re.compile(r"^/api/failover/prefer/(?P<index>\d{1,6})$"), self.failover_prefer),
            ("POST", re.compile(r"^/api/failover/restore$"), self.failover_restore),
            ("POST", re.compile(r"^/api/manual/(?P<step_id>[a-z_]{1,40})/done$"), self.manual_done),
            ("POST", re.compile(r"^/api/tweaks/batch$"), self.set_tweaks),     # before the per-tweak route
            ("POST", re.compile(r"^/api/tweaks/(?P<tweak_id>[^/]+)$"), self.set_tweak),
            ("POST", re.compile(r"^/api/diagnostics$"), self.start_diagnostics),
            ("POST", re.compile(r"^/api/diagnostics/bufferbloat$"), self.start_bufferbloat),
            ("GET", re.compile(r"^/api/diagnostics/runs$"), self.diagnostic_runs),
            ("GET", re.compile(r"^/api/diagnostics/runs/(?P<run_id>\d{1,12})$"), self.diagnostic_run),
            ("GET", re.compile(r"^/api/jobs/(?P<job_id>[0-9a-f]{32})$"), self.job),
            ("POST", re.compile(r"^/api/actions/(?P<name>[a-z_]+)$"), self.action),
            ("POST", re.compile(r"^/api/settings$"), self.update_settings),
        ]

    # -- dispatch ----------------------------------------------------------------------------

    def handle(self, method: str, path: str, query: dict[str, list[str]], body: Any) -> tuple[int, Any]:
        known_path = False
        for m, pattern, fn in self._routes:
            match = pattern.match(path)
            if match:
                known_path = True
                if m == method:
                    result = fn(query=query, body=body, **match.groupdict())
                    # Stored messages ({"key", "params"}, ADR-0006) leave as text in the chosen language.
                    return 200, i18n.localize(result, self.language(query))
        raise ApiError(405 if known_path else 404, "method not allowed" if known_path else "not found")

    def language(self, query: dict) -> str:
        """?lang= for one request, else settings ui.language."""
        if "lang" in query:
            return i18n.resolve(query["lang"][0])
        return i18n.resolve(self._load().get("ui", {}).get("language", i18n.DEFAULT))

    @staticmethod
    def _int(query: dict, name: str, default: int, lo: int, hi: int) -> int:
        raw = query.get(name, [str(default)])[0]
        try:
            return max(lo, min(hi, int(raw)))
        except ValueError:
            raise ApiError(400, f"{name} must be an integer") from None

    # -- defaults (real implementations) -----------------------------------------------------

    def _default_tweak_manager(self) -> Any:
        from . import tweaks
        return tweaks.default_manager(self.storage)

    @staticmethod
    def _default_autostart_status() -> Any:
        from . import autostart
        return autostart.status()

    @staticmethod
    def _default_elevate(op: str, value: str, **extra: Any) -> Any:
        from .elevation import run_elevated
        return run_elevated(op, value, **extra)

    @staticmethod
    def _default_measure_tweak(tweak_id: str) -> dict | None:
        from . import tweaks
        return tweaks.measure_for(next(t for t in tweaks.CATALOG if t.id == tweak_id))()

    @staticmethod
    def _default_network_id() -> str | None:
        from . import calibration
        return calibration.current_network_id()

    def _default_diagnostics(self, progress: Callable[[dict], None] | None = None) -> dict:
        from . import diagnostics

        # Every finished check is listed, not only the last: a poll can miss steps of fast checks.
        finished: list[dict] = []
        current: list[str] = []

        def report_progress(done: int, total: int, key: str | None, status: str | None) -> None:
            if current and status is not None:
                finished.append({"key": current[0], "title": diagnostics.title_of(current[0]), "status": status})
            current[:] = [key] if key else []
            if progress is not None:
                progress({"done": done, "total": total, "key": key,
                          "title": diagnostics.title_of(key) if key else None, "finished": list(finished)})
        report = diagnostics.run_all(diagnostics.Context(self.storage), progress=report_progress)
        run_id = diagnostics.save_report(self.storage, report)
        return {"run_id": run_id, "ts": report.ts, "worst": report.worst,
                "results": [r.to_dict() for r in report.results]}

    def _default_bufferbloat(self) -> dict:
        from . import diagnostics
        report = diagnostics.run_all(diagnostics.Context(self.storage), only={14})
        results = [r.to_dict() for r in report.results]
        self._last_bufferbloat = results
        return {"ts": report.ts, "worst": report.worst, "results": results}

    def _actions_obj(self) -> Any:
        if self._actions is None:
            from .actions import Actions
            self._actions = Actions()
        return self._actions

    # -- read endpoints ----------------------------------------------------------------------

    def state(self, **_: Any) -> dict:
        settings = self._load()
        return {"version": __version__, "time": self._clock(), "is_admin": bool(self._is_admin()),
                "watchdog": settings.get("watchdog", {}), "targets": settings.get("targets", {}),
                # the sections the UI may change (same as POST /api/settings accepts), plus read-only context
                "settings": {s: settings.get(s, {}) for s in SETTINGS_SCHEMA},
                "notify_after_s": settings.get("notify", {}).get("outage_after_s")}

    AUTOSTART_CACHE_S = 60

    def autostart(self, **_: Any) -> dict:
        """Whether the monitor starts at logon (read-only: schtasks /Query, cached a minute).
        Changing it is the installer's job."""
        cache = self._autostart_cache
        if cache is None or self._clock() - cache[0] > self.AUTOSTART_CACHE_S:
            st = self._autostart_status()
            cache = self._autostart_cache = (self._clock(), {"installed": st.installed, "problems": st.problems,
                                                             "legacy_shortcut": st.legacy_shortcut})
        return cache[1]

    def translations(self, query: dict, **_: Any) -> dict:
        """Everything the UI needs to render text: the chosen setting, the resolved language,
        the selectable languages and the full catalog (English filled in for missing keys)."""
        setting = self._load().get("ui", {}).get("language", i18n.DEFAULT)
        lang = self.language(query)   # ?lang= previews another one
        return {"setting": setting, "language": lang, "windows_language": i18n.windows_ui_language(),
                "available": i18n.available(), "messages": i18n.messages(lang)}

    def live(self, query: dict, **_: Any) -> dict:
        return self.monitor.snapshot(window_s=self._int(query, "window", 300, 1, 900))

    def history(self, query: dict, **_: Any) -> dict:
        """Minute stats, Wi-Fi stats and events. ?bucket=N (minutes) merges rows into N-minute
        buckets so a week fits in a chart without shipping 10 000 rows per target."""
        now = int(self._clock())
        start = now - self._int(query, "hours", 24, 1, 24 * 30) * 3600
        bucket = self._int(query, "bucket", 1, 1, 24 * 60)
        stats = self.storage.query_minute_stats(start, now + 60)
        wifi = self.storage.query_wifi_stats(start, now + 60)
        if bucket > 1:
            stats, wifi = bucket_minute_stats(stats, bucket * 60), bucket_wifi_stats(wifi, bucket * 60)
        return {"bucket_s": bucket * 60, "minute_stats": stats, "wifi_stats": wifi,
                "events": self.storage.query_events(since=start, limit=5000)}

    def events(self, query: dict, **_: Any) -> dict:
        return {"events": self.storage.query_events(limit=self._int(query, "limit", 200, 1, 2000))}

    def tweaks(self, **_: Any) -> dict:
        cache = self._tweak_cache
        stale = cache["refreshed_at"] is None or self._clock() - cache["refreshed_at"] > self.TWEAK_CACHE_S
        job = self._refresh_tweaks() if stale else None
        impact = {c["id"]: c["impact"] for c in reversed(self._changes()) if c["kind"] == "tweak" and not c["undone"]}
        return {"states": cache["states"], "refreshed_at": cache["refreshed_at"],
                "refreshing": bool(job and job["status"] == "running"), "impact": impact}

    def _changes(self) -> list[dict]:
        from . import impact, tweaks
        try:
            backup = self._load_backup()
        except Exception:   # unreadable backup.json: report from the log alone
            backup = {}
        from .suggestions import STEPS
        return impact.changes(self.storage, backup, self._clock(), tweak_names={t.id: t.name for t in tweaks.CATALOG},
                              step_names={s.id: s.title for s in STEPS.values()})

    def suggestions(self, **_: Any) -> dict:
        """Tweaks and manual steps the latest diagnostics call for, most serious first."""
        from . import suggestions, tweaks
        states = self._tweak_cache["states"]
        tweak_states = {s["id"]: s for s in states} if states else None
        meta = {t.id: {"name": t.name, "note": t.note, "risk": t.risk, "measured": isinstance(t, tweaks.MeasuredTweak),
                       "disrupts": t.disrupts_network} for t in tweaks.CATALOG}
        out = suggestions.build(self.storage, self._clock(), tweak_states, meta)
        from .diagnostics import CHECKS
        out["checks_total"] = len(CHECKS)   # "Runs N checks", before the first run
        if self._last_bufferbloat:
            extra = suggestions.suggest(self._last_bufferbloat, tweak_states, suggestions.done_steps(self.storage, self._clock()), meta)
            known = {(i["kind"], i["id"]) for i in out["items"]}
            out["items"] = sorted(out["items"] + [i for i in extra if (i["kind"], i["id"]) not in known],
                                  key=suggestions.order)
        return out

    def value(self, **_: Any) -> dict:
        """What the app did over the last 7 days, counted from the log (ADR-0020 point 8)."""
        from . import value
        return value.summarize(self.storage, self._clock(), self._changes())

    def failover_state(self, **_: Any) -> dict:
        """Paths, which one carries the traffic, whether we switched (ADR-0008)."""
        snap = self.failover.snapshot() if self.failover is not None else {"paths": [], "preferred": None}
        return {**snap, "settings": self._load().get("failover", {}), "is_admin": bool(self._is_admin())}

    def failover_prefer(self, index: str, **_: Any) -> dict:
        """The user asked to move to this path now. Admin: directly; otherwise through UAC.
        Refused for the path already in use, before any UAC prompt."""
        from . import failover

        def work() -> dict:
            route = self._route()
            name = next((p.name for p in getattr(self.failover, "paths", []) if p.index == int(index)), None)
            refusal = failover.in_use_refusal(int(index), route, name)
            if refusal is not None:
                return {"ok": False, "message": refusal, "elevated": False}
            if self._is_admin() and self.failover is not None:
                paths = failover.usable_paths(winutil.get_paths())
                backup = next((p for p in paths if p.index == int(index)), None)
                if backup is None:
                    return {"ok": False, "message": i18n.msg("failover.result.unknown_path", path=index)}
                primary = route["interface_index"] if route else None
                home = next((p for p in paths if p.index == primary and p.index != backup.index), None)
                res = self.failover.switch.prefer(backup, home, source=failover.user_source(self._load))
                failover.record_user_switch(self.storage, "prefer", res, self._clock)
                return {"ok": res.ok, "message": res.message, "elevated": False}
            res = self._elevate("path-prefer", str(int(index)))      # the helper logs it itself
            return {"ok": res.ok, "message": res.message, "cancelled": res.cancelled, "elevated": True}
        return self.jobs.submit("failover", work, single="failover", sync=self._sync)

    def failover_restore(self, **_: Any) -> dict:
        """Back to the original metrics (switch back, or clean up after turning failover off)."""
        def work() -> dict:
            if self._is_admin() and self.failover is not None:
                from . import failover
                results = self.failover.switch.restore_all()
                for res in results:
                    failover.record_user_switch(self.storage, "restore", res, self._clock)
                bad = [r for r in results if not r.ok]
                message = (bad[0] if bad else results[0]).message if results else i18n.msg("failover.result.nothing")
                return {"ok": not bad, "message": message, "elevated": False}
            res = self._elevate("path-restore", "all")
            return {"ok": res.ok, "message": res.message, "cancelled": res.cancelled, "elevated": True}
        return self.jobs.submit("failover", work, single="failover", sync=self._sync)

    def manual_done(self, step_id: str, **_: Any) -> dict:
        """The user says they did a manual step; its before/after comparison starts now."""
        from . import suggestions
        if step_id not in suggestions.STEPS:
            raise ApiError(404, "unknown manual step")
        return suggestions.mark_done(self.storage, step_id, self._clock())

    def impact(self, **_: Any) -> dict:
        """Recent changes (tweaks turned on, manual steps done) with before/after metrics (ADR-0007)."""
        return {"changes": self._changes()}

    def _refresh_tweaks(self) -> dict:
        def work() -> list:
            from . import calibration
            states = [asdict(s) for s in self._tweak_manager().states()]
            if any(s.get("measured") and s.get("enabled") for s in states):
                try:
                    network = self._network_id()
                except Exception:
                    network = None
                for s in states:
                    if s.get("measured") and s.get("enabled") and not s.get("measurement"):
                        s["stale"] = ["no_record"]   # on, but where its value came from is unknown
                    else:
                        s["stale"] = calibration.staleness(s.get("measurement"), network, self._clock())
            self._tweak_cache.update(states=states, refreshed_at=self._clock())
            return states
        return self.jobs.submit("tweak_states", work, single="tweak_states", sync=self._sync)

    def diagnostic_runs(self, query: dict, **_: Any) -> dict:
        return {"runs": self.storage.list_diagnostic_runs(self._int(query, "limit", 20, 1, 200))}

    def diagnostic_run(self, run_id: str, **_: Any) -> dict:
        run = self.storage.get_diagnostic_run(int(run_id))
        if run is None:
            raise ApiError(404, "no such diagnostic run")
        return run

    def job(self, job_id: str, **_: Any) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise ApiError(404, "no such job")
        return job

    # -- write endpoints ---------------------------------------------------------------------

    def start_diagnostics(self, **_: Any) -> dict:
        """The job's "progress" says which check runs now and how many are done (ADR-0020)."""
        return self.jobs.submit("diagnostics", self._run_diagnostics, single="diagnostics", sync=self._sync,
                                progress=True)

    def start_bufferbloat(self, **_: Any) -> dict:
        """Generates real traffic (~30 s), so only on explicit request; the result is not stored as a run."""
        def work() -> dict:
            with self._line_lock:
                return self._run_bufferbloat()
        return self.jobs.submit("bufferbloat", work, single="bufferbloat", sync=self._sync)

    def set_tweak(self, tweak_id: str, body: Any, **_: Any) -> dict:
        tweak_id = unquote(tweak_id)
        if not TWEAK_ID.match(tweak_id):
            raise ApiError(400, "bad tweak id")
        if not isinstance(body, dict) or not isinstance(body.get("enable"), bool):
            raise ApiError(400, 'body must be {"enable": true|false}')
        enable = body["enable"]

        def work() -> dict:
            refusal = None if not enable or self._is_measured(tweak_id) else self._tweak_manager().preflight(tweak_id)
            if refusal is not None:   # told before any UAC prompt (SIC-96); nothing was written
                result = {"ok": False, "changed": False, "message": refusal, "elevated": False}
            elif enable and self._is_measured(tweak_id):
                result = self._enable_measured(tweak_id)
            elif self._is_admin():
                mgr = self._tweak_manager()
                mgr.get(tweak_id)  # KeyError -> job error for an unknown id
                out = mgr.enable(tweak_id) if enable else mgr.disable(tweak_id)
                result = {"ok": out.ok, "changed": out.changed, "message": out.message, "elevated": False}
            else:
                res = self._elevate("tweak-enable" if enable else "tweak-disable", tweak_id)
                result = {"ok": res.ok, "message": res.message, "cancelled": res.cancelled, "elevated": True}
            self._tweak_cache["refreshed_at"] = None  # force a fresh read
            return result
        # One write at a time: two UAC prompts or two writers racing on backup.json would be confusing at best.
        return self.jobs.submit(f"tweak:{tweak_id}", work, single="tweak_write", sync=self._sync)

    def set_tweaks(self, body: Any, **_: Any) -> dict:
        """The Fix button (ADR-0020): several low-risk tweaks on (or back off) under one UAC prompt.
        Body {"ids": [...], "enable": true|false}; the ids are checked here and again by the helper."""
        from . import tweaks
        if not isinstance(body, dict) or not isinstance(body.get("enable"), bool) or not isinstance(body.get("ids"), list) \
                or not all(isinstance(i, str) and TWEAK_ID.match(i) for i in body["ids"]):
            raise ApiError(400, 'body must be {"ids": [tweak ids], "enable": true|false}')
        enable = body["enable"]
        try:
            chosen = tweaks.batch_tweaks(body["ids"])
        except ValueError as exc:
            raise ApiError(400, str(exc)) from None

        def work() -> dict:
            if self._is_admin():
                result = {**tweaks.run_batch(self._tweak_manager(), chosen, enable), "elevated": False}
            else:
                op = "tweak-enable-many" if enable else "tweak-disable-many"
                res = self._elevate(op, ",".join(t.id for t in chosen))
                detail = getattr(res, "result", None) or {}   # empty when UAC was declined or no result came back
                result = {"ok": res.ok, "message": res.message, "cancelled": res.cancelled, "elevated": True,
                          "results": detail.get("results", []), "changed": detail.get("changed")}
            self._tweak_cache["refreshed_at"] = None
            return result
        return self.jobs.submit("tweaks", work, single="tweak_write", sync=self._sync)

    @staticmethod
    def _is_measured(tweak_id: str) -> bool:
        from . import tweaks
        return any(t.id == tweak_id and isinstance(t, tweaks.MeasuredTweak) for t in tweaks.CATALOG)

    def _enable_measured(self, tweak_id: str) -> dict:
        """Measure, refuse before UAC when there is nothing to fix, enable, measure again (ADR-0016)."""
        from . import tweaks
        admin = self._is_admin()
        mgr = self._tweak_manager()

        def enable(measurement: dict) -> Any:
            if admin:
                return mgr.enable(tweak_id, measurement)
            res = self._elevate("tweak-enable", tweak_id, measurement=measurement)
            return SimpleNamespace(ok=res.ok, message=res.message, cancelled=res.cancelled,
                                   changed=bool((res.result or {}).get("changed", res.ok)))

        def record(kind: str, message: Any, level: str) -> None:
            self.storage.add_event(int(self._clock()), kind, message, level=level)

        def measure() -> dict | None:
            with self._line_lock:
                return self._measure_tweak(tweak_id)

        result = tweaks.enable_measured(mgr, tweak_id, measure, enable, record)
        return {**result, "elevated": not admin}

    def action(self, name: str, **_: Any) -> dict:
        if name not in ACTIONS:
            raise ApiError(404, "unknown action")
        snap = self.monitor.snapshot(window_s=0)
        wifi = snap.get("wifi") or {}
        interface = wifi.get("interface") or ""
        profile = wifi.get("profile") or snap.get("last_profile") or ""

        def work() -> dict:
            acts = self._actions_obj()
            if name == "reconnect":
                if not interface or not profile:
                    return {"ok": False, "message": msg("server.action.no_profile")}
                res = acts.reconnect(interface, profile, force=True)
            elif name == "restart_adapter":
                if not interface:
                    return {"ok": False, "message": msg("server.action.no_wifi_card")}
                if not self._is_admin():
                    res = self._elevate("restart-adapter", interface)
                    return {"ok": res.ok, "message": res.message, "cancelled": res.cancelled, "elevated": True}
                res = acts.restart_adapter(interface)
            elif name == "flush_dns":
                res = acts.flush_dns()
            else:
                if not interface:
                    return {"ok": False, "message": msg("server.action.no_wifi_card")}
                res = acts.renew_dhcp(interface)
            self.storage.add_event(int(self._clock()), "user_action",
                                   msg("event.user_action", action=name, result=res.message),
                                   level="info" if res.ok else "warn")
            return {"ok": res.ok, "message": res.message}
        return self.jobs.submit(f"action:{name}", work, single="action", sync=self._sync)

    def update_settings(self, body: Any, **_: Any) -> dict:
        if not isinstance(body, dict) or not body:
            raise ApiError(400, "body must be a non-empty JSON object")
        changes: dict[str, dict[str, Any]] = {}
        for section, values in body.items():
            schema = SETTINGS_SCHEMA.get(section)
            if schema is None or not isinstance(values, dict):
                raise ApiError(400, f"setting section {section!r} cannot be changed here")
            for key, value in values.items():
                rule = schema.get(key)
                if rule is None:
                    raise ApiError(400, f"unknown setting {section}.{key}")
                types = rule[0]
                # bool is a subclass of int in Python: never let true/false pass as a number.
                type_ok = isinstance(value, bool) if types is bool else (
                    isinstance(value, types) and not isinstance(value, bool))
                if not type_ok:
                    raise ApiError(400, f"{section}.{key} has the wrong type")
                if len(rule) == 2 and callable(rule[1]) and value not in rule[1]():
                    raise ApiError(400, f"{section}.{key} must be one of {rule[1]()}")
                if len(rule) == 3 and not rule[1] <= value <= rule[2]:
                    raise ApiError(400, f"{section}.{key} must be between {rule[1]} and {rule[2]}")
                changes.setdefault(section, {})[key] = value
        with self._settings_lock:
            settings = self._load()
            for section, values in changes.items():
                settings.setdefault(section, {}).update(values)
            for section in ("watchdog", "failover"):
                if changes.get(section, {}).get("enabled") is True:
                    settings[section]["tripped_at"] = None  # the user re-enabled it after a trip
            self._save(settings)
        if "ui" in changes:
            i18n.invalidate()   # toasts and API texts switch language right away
        self.storage.add_event(int(self._clock()), "settings_changed",
                               msg("event.settings_changed", changes=json.dumps(changes, ensure_ascii=False)),
                               level="info")
        return {"ok": True, "settings": {s: settings.get(s) for s in SETTINGS_SCHEMA}}


def bucket_minute_stats(rows: list[dict], size_s: int) -> list[dict]:
    """Merge per-minute ping rows into `size_s` buckets per target: sums for sent/lost, the
    reply-weighted mean for avg, the max of max, the mean of jitter."""
    groups: dict[tuple[int, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["ts"] - r["ts"] % size_s, r["target"]), []).append(r)
    out = []
    for (ts, target), rs in sorted(groups.items()):
        sent, lost = sum(r["sent"] for r in rs), sum(r["lost"] for r in rs)
        weighted = [(r["avg"], r["sent"] - r["lost"]) for r in rs if r.get("avg") is not None and r["sent"] > r["lost"]]
        replies = sum(w for _, w in weighted)
        jitters = [r["jitter"] for r in rs if r.get("jitter") is not None]
        maxes = [r["max"] for r in rs if r.get("max") is not None]
        out.append({"ts": ts, "target": target, "ip": rs[-1].get("ip"), "sent": sent, "lost": lost,
                    "avg": round(sum(a * w for a, w in weighted) / replies, 2) if replies else None,
                    "max": max(maxes) if maxes else None,
                    "jitter": round(sum(jitters) / len(jitters), 2) if jitters else None})
    return out


def bucket_wifi_stats(rows: list[dict], size_s: int) -> list[dict]:
    """Keep the last Wi-Fi row of each bucket (state, SSID...) with mean signal/RSSI/rates."""
    groups: dict[int, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["ts"] - r["ts"] % size_s, []).append(r)
    out = []
    for ts, rs in sorted(groups.items()):
        row = dict(rs[-1], ts=ts)
        for key in ("signal", "rssi", "rx_mbps", "tx_mbps"):
            values = [r[key] for r in rs if r.get(key) is not None]
            row[key] = round(sum(values) / len(values), 1) if values else None
        out.append(row)
    return out


# --- HTTP layer ---------------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False   # never share the port with another process

    def __init__(self, addr: tuple[str, int], api: Api, token: str) -> None:
        super().__init__(addr, _Handler)
        self.api, self.token = api, token
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}


class _Handler(BaseHTTPRequestHandler):
    server: _Server
    server_version = "AmbystoSteady"
    sys_version = ""
    timeout = 10

    def log_message(self, fmt: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- responses ----------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False, default=_jsonable).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- the gate -----------------------------------------------------------------------

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _token_ok(self) -> bool:
        given = self.headers.get("X-Token", "")
        return hmac.compare_digest(given.encode("utf-8"), self.server.token.encode("utf-8"))

    def _write_origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.server.allowed_origins:
            return False
        return self.headers.get("Sec-Fetch-Site", "").lower() not in ("cross-site", "same-site")

    # -- verbs --------------------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # refuse preflights: a cross-origin caller never gets a CORS grant
        self._error(403, "forbidden")

    def do_PUT(self) -> None:
        self._error(405, "method not allowed")

    do_DELETE = do_PATCH = do_PUT

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    DRAIN_MAX = 1_000_000      # most unread request body we will swallow before answering an error
    DRAIN_SECONDS = 3.0

    def _drain(self, n: int) -> None:
        """Swallow (a bounded amount of) the unread request body. Closing a socket with unread data
        makes TCP send RST, which can destroy our error response so the client sees "connection
        aborted" instead of the status. Bounded in bytes and time so a slow sender cannot hold a thread."""
        deadline = time.monotonic() + self.DRAIN_SECONDS
        remaining = min(n, self.DRAIN_MAX)
        try:
            self.connection.settimeout(1.0)
            while remaining > 0 and time.monotonic() < deadline:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        if n > self.DRAIN_MAX:
            self.close_connection = True   # more left than we are willing to read: do not reuse the connection

    def _refuse(self, status: int, message: str) -> None:
        """Error before the body was read: drain it first, then answer."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > 0:
            self._drain(n)
        self._error(status, message)

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                return self._refuse(403, "bad host")
            parts = urlsplit(self.path)
            path = parts.path
            if not path.startswith("/api/"):
                if method != "GET":
                    return self._refuse(405, "method not allowed")
                return self._static(path)
            if not self._token_ok():
                return self._refuse(401, "missing or bad token")
            body = None
            if method == "POST":
                if not self._write_origin_ok():
                    return self._refuse(403, "cross-origin write refused")
                body = self._read_json()
                if body is _BAD:
                    return
            status, payload = self.server.api.handle(method, path, parse_qs(parts.query), body)
            self._json(status, payload)
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except Exception:
            log.exception("request failed: %s %s", method, self.path)
            self._error(500, "internal error")

    def _read_json(self) -> Any:
        length = self.headers.get("Content-Length")
        if length is None:
            self._error(411, "Content-Length required")
            return _BAD
        try:
            n = int(length)
        except ValueError:
            self._error(400, "bad Content-Length")
            return _BAD
        if n < 0 or n > MAX_BODY:
            if n > 0:
                self._drain(n)
            self._error(413, "body too large")
            return _BAD
        if "application/json" not in self.headers.get("Content-Type", ""):
            self._error(415, "Content-Type must be application/json")
            return _BAD
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._error(400, "invalid JSON")
            return _BAD

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            index = WEB_ROOT / "index.html"
            try:
                html = index.read_text(encoding="utf-8")
            except OSError:
                return self._error(404, "UI not installed")
            # The token is handed only to pages served by us, behind the Host check.
            return self._send(200, html.replace("{{TOKEN}}", self.server.token).encode("utf-8"),
                              "text/html; charset=utf-8")
        rel = unquote(path).lstrip("/")
        if "\\" in rel or "\x00" in rel:
            return self._error(404, "not found")
        target = (WEB_ROOT / rel).resolve()
        root = WEB_ROOT.resolve()
        if root not in target.parents or target.suffix.lower() not in STATIC_TYPES or not target.is_file():
            return self._error(404, "not found")
        self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix.lower()])


_BAD = object()


class Server:
    """Owns the listening socket and the runtime file that tells launchers where we are."""

    def __init__(self, api: Api, port: int, host: str = "127.0.0.1") -> None:
        if host != "127.0.0.1":
            raise ValueError("the server only listens on 127.0.0.1")
        self.token = secrets.token_urlsafe(32)
        self.httpd = _Server((host, port), api, self.token)
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def runtime_file(self) -> Path:
        return config.user_dir() / "server.json"

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True)
        self._thread.start()
        payload = {"url": self.url, "port": self.port, "pid": os.getpid(), "started": int(time.time())}
        self.runtime_file().write_text(json.dumps(payload), encoding="utf-8")

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        try:
            data = json.loads(self.runtime_file().read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                self.runtime_file().unlink()
        except (OSError, ValueError):
            pass
