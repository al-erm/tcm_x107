"""
Entry point for TCM-X107 + Keithley 6487 application.
"""

import sys
import os
import platform

# --- GTK/GDK backend selection -----------------------------------
# In WSL/Wayland environments, forcing X11 breaks the graphical display.
# If GDK_BACKEND is not set, choose a suitable backend automatically:
system = platform.system()
if system == "Windows":
    pass  # GTK will choose the backend itself
elif (os.environ.get("TERM_PROGRAM") == "vscode" and "WSL_DISTRO_NAME" not in os.environ
      and "WSL_INTEROP" not in os.environ):
    # VS Code (and some other IDEs) may pass WAYLAND_DISPLAY,
    # but not provide correct GTK operation in Wayland mode.
    os.environ.setdefault("GDK_BACKEND", "x11")
else:
    os.environ.setdefault("GDK_BACKEND", "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11")

# --- Suppress Qt Wayland noise ------------------------------------
# qt.qpa.wayland.textinput logs spam about input surface focus to stderr.
# This is not a program error, just noise in the console.
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland.textinput=false")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
import gui.themes as themes  # noqa: E402


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("TCM-X107 + Keithley 6487")
    # Use explicit Fusion style so QStyleHints.setColorScheme() works
    # correctly on all platforms (including Windows).
    app.setStyle("Fusion")
    themes.apply_theme(app, "system")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
