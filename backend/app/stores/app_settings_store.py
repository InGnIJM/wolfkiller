"""Application-level settings store (``data/settings.json``).

Operator switches live in their own file instead of ``models.json``: that file
holds model credentials, and a settings entry written there would surface as a
selectable model in ``GET /api/models``.

The store is deliberately **not** a generic key/value bag. Every setting gets a
typed accessor pair, so adding one is an explicit review rather than a silent
append, and callers can never read a key nobody agreed on.

All failures are best-effort: a missing, corrupt, or unwritable file reads as
"not set" and never propagates to the caller. A settings problem must not take
a game down.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "settings.json"
APP_SETTINGS_PATH_ENV = "APP_SETTINGS_PATH"
_PROMPT_CAPTURE_KEY = "prompt_capture_enabled"


class AppSettingsStore(Protocol):
    def get_prompt_capture_enabled(self) -> Optional[bool]: ...
    def set_prompt_capture_enabled(self, enabled: Optional[bool]) -> None: ...


class JsonAppSettingsStore:
    """JSON-file implementation; writes are atomic (tmp file + ``os.replace``)."""

    VERSION = 1

    def __init__(self, path: str | None = None):
        self._path = Path(path or _default_path())
        self._lock = threading.RLock()

    def get_prompt_capture_enabled(self) -> Optional[bool]:
        """The stored override, or ``None`` when the operator has not set one."""
        with self._lock:
            value = self._read().get(_PROMPT_CAPTURE_KEY)
        # ``True``/``False`` only: a hand-edited ``1`` or ``"true"`` is not a
        # decision the UI ever wrote, so it must not silently win over env.
        return value if type(value) is bool else None

    def set_prompt_capture_enabled(self, enabled: Optional[bool]) -> None:
        """Store the override; ``None`` clears it so env/default apply again."""
        with self._lock:
            settings = self._read()
            if enabled is None:
                settings.pop(_PROMPT_CAPTURE_KEY, None)
            else:
                settings[_PROMPT_CAPTURE_KEY] = enabled
            self._write(settings)

    def _read(self) -> dict:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except OSError as error:
            logger.warning("Failed to load app settings, starting empty: %s", error)
            return {}
        except json.JSONDecodeError as error:
            logger.warning("Failed to load app settings, starting empty: %s", error)
            return {}
        if not isinstance(raw, dict):
            logger.warning("App settings root is not an object, starting empty")
            return {}
        settings = raw.get("settings")
        if not isinstance(settings, dict):
            logger.warning("App settings section is not an object, starting empty")
            return {}
        return settings

    def _write(self, settings: dict) -> None:
        try:
            os.makedirs(self._path.parent, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": self.VERSION, "settings": settings},
                    handle, ensure_ascii=False, indent=2,
                )
            os.replace(tmp, self._path)
        except OSError as error:
            # Best effort by contract: the caller is an operator toggle, and a
            # failed write must not turn into a 500 on a read endpoint.
            logger.warning("Failed to persist app settings: %s", error)


def _default_path() -> str:
    data_dir = os.getenv("WOLFKILLER_DATA_DIR", "data")
    return str(Path(data_dir).expanduser() / SETTINGS_FILENAME)


_store: Optional[AppSettingsStore] = None


def get_app_settings_store() -> AppSettingsStore:
    """Lazy singleton; path overridable via ``APP_SETTINGS_PATH`` env var."""
    global _store
    if _store is None:
        _store = JsonAppSettingsStore(
            os.getenv(APP_SETTINGS_PATH_ENV, _default_path()),
        )
    return _store
