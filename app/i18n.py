"""Translations (ADR-0006).

    t("notify.back.title")                       -> text in the current language
    t("time.seconds", count=5)                   -> plural-aware
    msg("diag.signal.weak", rssi=-75)            -> {"key": ..., "params": ...} to store and render later
    render(stored_message_or_plain_text, lang)   -> text

Catalogs: app/locales/<BCP 47 code>.json, one flat object of key -> template (or plural dict),
plus "_meta": {"name": native name, "english_name": ...}. English is the reference and fallback.
Templates accept only {name} or {name:format_spec}; anything else stays literal, so a
translation can never reach into Python objects.
"""
from __future__ import annotations

import ctypes
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from . import config

log = logging.getLogger("stableinternet.i18n")

LOCALES_DIR = Path(__file__).with_name("locales")
DEFAULT = "en"
AUTO = "auto"

_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}")
# Width and precision are capped so a bad catalog cannot ask for a megabyte of padding.
_SAFE_SPEC = re.compile(r"^[<>=^]?[+\- ]?#?0?\d{0,3}[,_]?(?:\.\d{1,2})?[bcdeEfFgGnosxX%]?$")
_DECIMAL_COMMA = {"vi", "fr", "de", "es", "pt", "it", "ru", "id", "tr", "nl", "pl", "cs", "uk", "sv", "da", "fi",
                  "nb", "ro", "hu", "el"}
_ONE_IF_ONE = {"en", "de", "es", "it", "pt", "nl", "sv", "da", "nb", "fi", "el", "hu", "tr", "bg"}
_ONE_IF_ZERO_OR_ONE = {"fr"}

_lock = threading.Lock()
_catalogs: dict[str, dict[str, Any]] | None = None
_warned: set[str] = set()
_current: tuple[float, str] | None = None   # (expires, code)
CURRENT_TTL_S = 5.0


# --- catalogs -------------------------------------------------------------------------

def _load() -> dict[str, dict[str, Any]]:
    global _catalogs
    with _lock:
        if _catalogs is None:
            found = {}
            for path in sorted(LOCALES_DIR.glob("*.json")):
                try:
                    found[path.stem] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    log.error("cannot load %s: %r", path.name, exc)
            if DEFAULT not in found:
                found[DEFAULT] = {}
            _catalogs = found
        return _catalogs


def reload() -> None:
    """Forget loaded catalogs and the cached language (tests, or after adding a file)."""
    global _catalogs, _current
    with _lock:
        _catalogs, _current = None, None


def available() -> list[dict[str, str]]:
    """[{code, name, english_name}], English first, then by English name."""
    out = []
    for code, cat in _load().items():
        meta = cat.get("_meta", {})
        out.append({"code": code, "name": meta.get("name", code), "english_name": meta.get("english_name", code)})
    return sorted(out, key=lambda x: (x["code"] != DEFAULT, x["english_name"]))


def _base(code: str) -> str:
    return code.split("-")[0].lower()


def resolve(code: str | None) -> str:
    """Best available catalog for a requested code: exact (any case), then same base language,
    then English. "auto" asks Windows for the UI language."""
    if not code or code == AUTO:
        code = windows_ui_language() or DEFAULT
    cats = _load()
    by_lower = {c.lower(): c for c in cats}
    if code.lower() in by_lower:
        return by_lower[code.lower()]
    for c in cats:
        if _base(c) == _base(code):
            return c
    return DEFAULT


def windows_ui_language() -> str | None:
    """The Windows display language as a BCP 47 tag ("vi-VN"), or None if unavailable."""
    try:
        kernel32 = ctypes.WinDLL("kernel32")
        langid = kernel32.GetUserDefaultUILanguage()
        buf = ctypes.create_unicode_buffer(85)
        if kernel32.LCIDToLocaleName(langid, buf, 85, 0):
            return buf.value or None
    except (AttributeError, OSError):
        pass
    return None


def current_language() -> str:
    """Resolved language from settings.json -> ui.language, cached a few seconds."""
    global _current
    now = time.monotonic()
    cached = _current
    if cached is not None and cached[0] > now:
        return cached[1]
    try:
        setting = config.load_settings().get("ui", {}).get("language", DEFAULT)
    except Exception:
        setting = DEFAULT
    code = resolve(setting)
    _current = (now + CURRENT_TTL_S, code)
    return code


def invalidate() -> None:
    """Call after changing ui.language so the next lookup re-reads settings."""
    global _current
    _current = None


def messages(lang: str | None = None) -> dict[str, Any]:
    """Whole catalog for one language with English filled in for missing keys (for the UI)."""
    cats = _load()
    lang = resolve(lang) if lang else current_language()
    merged = {k: v for k, v in cats.get(DEFAULT, {}).items() if not k.startswith("_")}
    merged.update({k: v for k, v in cats.get(lang, {}).items() if not k.startswith("_")})
    return merged


# --- formatting -----------------------------------------------------------------------

def plural_category(lang: str, count: Any) -> str:
    try:
        n = abs(float(count))
    except (TypeError, ValueError):
        return "other"
    base = _base(lang)
    if base in _ONE_IF_ONE:
        return "one" if n == 1 else "other"
    if base in _ONE_IF_ZERO_OR_ONE:
        return "one" if n < 2 else "other"
    return "other"   # vi, zh, ja, ko, th, ... have no plural forms


def _lookup(key: str, lang: str) -> Any:
    cats = _load()
    for code in (lang, DEFAULT):
        value = cats.get(code, {}).get(key)
        if value is not None:
            return value
    if key not in _warned:
        _warned.add(key)
        log.warning("missing translation key %r", key)
    return None


def _format_value(value: Any, spec: str | None, lang: str) -> str:
    if is_message(value):
        return render(value, lang)
    if isinstance(value, (list, tuple)):   # a list parameter renders as "a, b, c"
        return ", ".join(_format_value(v, spec, lang) for v in value)
    if spec is not None and spec != "":
        if not _SAFE_SPEC.match(spec):
            return str(value)
        try:
            text = format(value, spec)
        except (ValueError, TypeError):
            return str(value)
    else:
        text = str(value)
    if isinstance(value, float) and not isinstance(value, bool) and _base(lang) in _DECIMAL_COMMA:
        text = text.replace(",", " ").replace(".", ",")   # 1,234.5 -> 1 234,5
    return text


def _fill(template: str, params: Mapping[str, Any], lang: str) -> str:
    def repl(m: re.Match) -> str:
        name, spec = m.group(1), m.group(2)
        if name not in params:
            return m.group(0)
        return _format_value(params[name], spec, lang)
    return _PLACEHOLDER.sub(repl, template)


def t(key: str, lang: str | None = None, **params: Any) -> str:
    """Translate `key`. Never raises: a missing key renders as the key itself."""
    lang = resolve(lang) if lang else current_language()
    template = _lookup(key, lang)
    if template is None:
        return key
    if isinstance(template, dict):
        template = template.get(plural_category(lang, params.get("count")), template.get("other", ""))
    if not isinstance(template, str):
        return key
    return _fill(template, params, lang)


def msg(key: str, **params: Any) -> dict[str, Any]:
    """A message to store now and render later in whatever language is chosen then."""
    return {"key": key, "params": params}


def is_message(value: Any) -> bool:
    """True for what msg() builds: a mapping with exactly "key" (str) and "params"."""
    return isinstance(value, Mapping) and set(value) == {"key", "params"} and isinstance(value["key"], str)


def localize(value: Any, lang: str | None = None) -> Any:
    """Copy of a JSON-like tree with every message rendered as text (API responses, CLI output).
    The input is never modified."""
    if is_message(value):
        return render(value, lang)
    if isinstance(value, Mapping):
        return {k: localize(v, lang) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [localize(v, lang) for v in value]
    return value


def render(message: Any, lang: str | None = None) -> str:
    """Render a stored message ({"key", "params"}) or pass legacy plain text through."""
    if is_message(message):
        params = message.get("params") or {}
        return t(message["key"], lang, **(params if isinstance(params, Mapping) else {}))
    if message is None:
        return ""
    return str(message)


def format_duration(seconds: float, lang: str | None = None) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h and not m:
        return t("time.hours", lang, count=h)
    if h:
        return t("time.hours_minutes", lang, hours=t("time.hours", lang, count=h),
                 minutes=t("time.minutes", lang, count=m))
    if m:
        if s:
            return t("time.minutes_seconds", lang, minutes=t("time.minutes", lang, count=m),
                     seconds=t("time.seconds", lang, count=s))
        return t("time.minutes", lang, count=m)
    return t("time.seconds", lang, count=s)
