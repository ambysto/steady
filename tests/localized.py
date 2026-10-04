"""Test helper: the diagnostics module with `evaluate_*` returning text instead of messages.

Most diagnostics tests were written against the Vietnamese wording, which app/locales/vi.json
keeps word for word, so they read results rendered in Vietnamese. English rendering and catalog
completeness are covered in tests/test_diagnostics.py (LocalizationTests) and tests/test_i18n.py.
"""
from typing import Any

from app import diagnostics


class DiagnosticsIn:
    def __init__(self, lang: str) -> None:
        self._lang = lang

    def __getattr__(self, name: str) -> Any:
        attr = getattr(diagnostics, name)
        if name.startswith("evaluate_"):
            return lambda *args, **kwargs: attr(*args, **kwargs).localized(self._lang)
        return attr
