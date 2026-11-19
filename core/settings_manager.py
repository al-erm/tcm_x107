"""
User settings management for the application.
Settings are stored in settings.json in the core/ folder.
The file is created automatically on first save.

Default values are taken from config.py (if the corresponding constant
exists there) to avoid duplication. Other defaults (language, theme,
window size, profile parameters) are UI-specific and defined here.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Ensure project root is visible for config import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    TCM_PORT,
    KEITHLEY_VISA_ADDRESS,
)

# Path to settings file – in core/ folder (next to this module)
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# Default values: priority – config.py, then local defaults
DEFAULTS: dict[str, Any] = {
    "language": "en",
    "theme": "system",
    "tcm_port": TCM_PORT,
    "keithley_visa_address": KEITHLEY_VISA_ADDRESS,
    # Temperature profile and measurements (UI-specific, not in config.py)
    "target_temp": 25.0,
    "ramp_speed": 1.0,
    "hold_time": 60,
    "meas_count": 1,
    # UI-only options
    "show_samples": False,
    "window_width": 1400,
    "window_height": 900,
}


class SettingsManager:
    """Loads, stores, and saves settings in a JSON file."""

    def __init__(self, filename: str | None = None) -> None:
        self.filename = filename or SETTINGS_FILE
        self._settings: dict[str, Any] = dict(DEFAULTS)
        self._autosave = True
        self.load()

    def load(self) -> None:
        """Load settings from the file.

        If the file is missing or corrupt – keep default values.
        """
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    # Replace only known keys, ignore extra/corrupt ones
                    for key in DEFAULTS:
                        if key in data:
                            self._settings[key] = data[key]
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                # File is corrupt – use default values
                pass

    def save(self) -> None:
        """Save current settings to file (creates file if missing)."""
        with open(self.filename, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._settings.get(key, default)

    def set(self, key: str, value: Any, autosave: bool | None = None) -> None:
        """Set the value of a setting.

        By default, saves immediately to file (unless autosave is disabled).
        """
        self._settings[key] = value
        should_save = self._autosave if autosave is None else autosave
        if should_save:
            self.save()

    def __getitem__(self, key: str) -> Any:
        return self._settings[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    @property
    def autosave(self) -> bool:
        return self._autosave

    @autosave.setter
    def autosave(self, value: bool) -> None:
        self._autosave = bool(value)
