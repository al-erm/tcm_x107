"""
App themes for TCM-X107 + Keithley 6487.
Supported values: 'system', 'light', 'dark'.

Uses QStyleHints.setColorScheme() (Qt 6) together with Fusion style:
the whole app color scheme changes at once – windows, checkboxes,
buttons, input fields, borders – without manual color definitions
via QPalette/QSS.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont


DEFAULT_FONT = QFont("Sans Serif", 9)


def apply_theme(app: QApplication, theme: str) -> None:
    """
    Apply the app theme.
    theme: 'system' | 'light' | 'dark'

    'system' – reset color scheme (system default).
    'light'  – Qt.ColorScheme.Light.
    'dark'   – Qt.ColorScheme.Dark.

    Works with Fusion style (default in PyQt6 on Linux, can be set
    explicitly via app.setStyle('Fusion') on Windows).
    """
    if theme not in ("system", "light", "dark"):
        raise ValueError(f"Unknown theme: {theme!r}")

    hints = app.styleHints()
    if theme == "system":
        hints.unsetColorScheme()
    elif theme == "light":
        hints.setColorScheme(Qt.ColorScheme.Light)
    elif theme == "dark":
        hints.setColorScheme(Qt.ColorScheme.Dark)

    app.setFont(DEFAULT_FONT)


# --- Colors for plots (pyqtgraph-compatible RGB tuples) ---
# Defined here, so they can be changed programmatically in one place.
PLOT_COLOR_TEMP = (210, 70, 70)
PLOT_COLOR_CURRENT = (80, 150, 230)
PLOT_COLOR_CURRENT_TEMP = (70, 180, 110)
PLOT_COLOR_FUTURE = (150, 150, 150)
