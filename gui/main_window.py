"""
Main application window: one panel with TCM-X107, Keithley 6487 controls and graphs.
"""

from __future__ import annotations

import sys
import os
import time
from datetime import datetime

import numpy as np

# Ensure project root is visible for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import QTimer, QObject, QThread, pyqtSignal, pyqtSlot  # noqa: E402
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QGroupBox, QLabel, QLineEdit, QPushButton, QDoubleSpinBox,
    QSpinBox, QCheckBox, QMessageBox, QDialog, QStatusBar, QTabWidget, QComboBox,
)  # noqa: E402
from PyQt6.QtGui import QFont, QFontMetrics  # noqa: E402

import pyqtgraph as pg  # noqa: E402
import gui.i18n as i18n  # noqa: E402

from config import (
    TCM_PORT, TCM_BAUDRATE, TCM_TIMEOUT, TCM_WRITE_TIMEOUT,
    KEITHLEY_VISA_ADDRESS, KEITHLEY_TIMEOUT_MS,
    SAFETY_MIN_TEMP, SAFETY_MAX_TEMP, UPDATE_INTERVAL_MS, PLOT_MAX_POINTS,
    TEMP_STATUS_THRESHOLD, EXPORT_FILENAME, EXPORT_DIR
)  # noqa: E402

from instruments.tcm_x107 import TCMX107, TCMX107Error  # noqa: E402
from instruments.keithley_6487 import Keithley6487, Keithley6487Error  # noqa: E402
from core.controller import TemperatureController  # noqa: E402
from core.data_manager import DataManager  # noqa: E402
from core.math_processing import robust_average_current  # noqa: E402
from core.settings_manager import SettingsManager  # noqa: E402

from gui.i18n import tr  # noqa: E402
import gui.themes as themes  # noqa: E402


# Below this window height the samples plot in the left panel is hidden
# automatically so the window can be resized down to its minimum height.
SAMPLES_PLOT_MIN_HEIGHT = 670


class MeasurementWorker(QObject):
    data_ready = pyqtSignal(float, float, float, float)
    samples_ready = pyqtSignal(object, float, float, object)
    error_occurred = pyqtSignal(str)
    measure_requested = pyqtSignal()

    def __init__(self, tcm: TCMX107, keithley: Keithley6487,
                 data_manager: DataManager, parent=None) -> None:
        super().__init__(parent)
        self.tcm = tcm
        self.keithley = keithley
        self.data_manager = data_manager
        self._running = False
        self._measure_interval = 0.5
        self._buffer_count = 1

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def set_interval(self, interval: float) -> None:
        self._measure_interval = max(0.1, interval)

    def set_buffer_count(self, count: int) -> None:
        """Number of points to acquire in one Keithley buffer."""
        self._buffer_count = max(1, count)

    @pyqtSlot()
    def do_measurement(self) -> None:
        """Slot invoked in the worker thread via queued connection.

        Keeps run_once() off the GUI thread so the window stays responsive.
        """
        self.run_once()

    def _get_temp_status(self, temp: float) -> int:
        """Determine heating / cooling / stable by comparing with target temp.

        Threshold: TEMP_STATUS_THRESHOLD °C around the target.
        Returns: -1 = cooling (above target), 0 = stable, 1 = heating (below target).
        Returns 0 if TCM is not available or target temperature cannot be read.
        """
        try:
            target = self.tcm.get_adjust_temp()
            diff = temp - target
            if diff > TEMP_STATUS_THRESHOLD:
                return -1   # cooling
            elif diff < -TEMP_STATUS_THRESHOLD:
                return 1    # heating
            else:
                return 0    # stable
        except Exception:
            return 0

    def run_once(self) -> None:
        if not self._running:
            return
        try:
            temp = self.tcm.get_actual_temp()

            if self.keithley is not None and self.keithley.is_connected:
                self.keithley.configure_buffer(self._buffer_count)
                currents = self.keithley.read_buffer()
                curr, curr_err, outlier_indices = robust_average_current(currents)
            else:
                curr, curr_err = 0.0, 0.0
                currents = []
                outlier_indices = []

            status = self._get_temp_status(temp)

            self.data_manager.log_point(
                temperature=temp,
                current=curr,
                current_error=curr_err,
                status=status,
            )
            pt = self.data_manager.points[-1]
            self.data_ready.emit(pt.timestamp, temp, curr, curr_err)
            # Emit raw samples for the left-panel plot (residuals + outliers)
            self.samples_ready.emit(currents, curr, curr_err, list(outlier_indices))
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.resize(1400, 900)

        self.tcm = None
        self.keithley = None
        self.controller = None
        self.data_manager = DataManager()

        self._tcm_model = ""
        self._keithley_ident = ""
        self._current_theme = "system"  # stores current theme for saving when language changes
        self._show_samples_desired = False  # samples plot: user choice, restored from settings
        self._worker = None
        self._worker_thread = None
        self._gui_timer = None
        self._measure_timer = None

        self.settings = SettingsManager()

        self._build_ui()
        self._apply_settings_to_ui()
        self._fill_lang_combo()
        self._fill_theme_combo()
        self._retranslate_ui()
        self._apply_styles()
        self._connect_settings_signals()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        splitter = QSplitter()
        main_layout.addWidget(splitter)

        # === Left panel: controls ===
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_panel.setMaximumWidth(420)

        # --- Language and theme selector (top-left of window) -------------------
        lang_layout = QHBoxLayout()
        self.lang_label = QLabel()
        self.lang_combo = QComboBox()
        self._fill_lang_combo()
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        # Auto-width based on longest language name
        all_lang_labels = [name for _code, name in i18n.get_available_languages()]
        fm = QFontMetrics(self.lang_combo.font())
        max_w = max((fm.horizontalAdvance(s) for s in all_lang_labels), default=0)
        self.lang_combo.setMinimumWidth(max_w + 30)
        lang_layout.addWidget(self.lang_label)
        lang_layout.addWidget(self.lang_combo)

        self.theme_label = QLabel()
        self.theme_combo = QComboBox()
        # Auto-width based on longest theme name across all languages
        all_theme_labels = []
        for _lang_dict in i18n.TRANSLATIONS.values():
            for _k in ("theme.system", "theme.light", "theme.dark"):
                if _k in _lang_dict:
                    all_theme_labels.append(_lang_dict[_k])
        fm = QFontMetrics(self.theme_combo.font())
        max_w = max((fm.horizontalAdvance(s) for s in all_theme_labels), default=0)
        self.theme_combo.setMinimumWidth(max_w + 30)  # margin for arrow and padding
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        lang_layout.addWidget(self.theme_label)
        lang_layout.addWidget(self.theme_combo)
        lang_layout.addStretch()
        left_layout.addLayout(lang_layout)

        # --- TCM-X107 ---
        self.tcm_group = QGroupBox()
        tcm_layout = QVBoxLayout()

        conn_layout = QHBoxLayout()
        self.btn_connect_tcm = QPushButton()
        self.btn_connect_tcm.setCheckable(True)
        self.btn_connect_tcm.clicked.connect(self._toggle_tcm_connection)
        conn_layout.addWidget(self.btn_connect_tcm)
        self.lbl_tcm_status = QLabel()
        self.lbl_tcm_status.setStyleSheet("color: red; font-weight: bold;")
        conn_layout.addWidget(self.lbl_tcm_status)
        tcm_layout.addLayout(conn_layout)

        port_layout = QHBoxLayout()
        self.lbl_port = QLabel()
        port_layout.addWidget(self.lbl_port)
        self.edit_port = QLineEdit(TCM_PORT)
        port_layout.addWidget(self.edit_port)
        tcm_layout.addLayout(port_layout)

        tgt_layout = QHBoxLayout()
        self.lbl_target_temp = QLabel()
        tgt_layout.addWidget(self.lbl_target_temp)
        self.spin_target_temp = QDoubleSpinBox()
        self.spin_target_temp.setRange(-50, 200)
        self.spin_target_temp.setValue(25.0)
        self.spin_target_temp.setDecimals(2)
        tgt_layout.addWidget(self.spin_target_temp)
        self.btn_set_temp = QPushButton()
        self.btn_set_temp.clicked.connect(self._set_target_temp)
        tgt_layout.addWidget(self.btn_set_temp)
        tcm_layout.addLayout(tgt_layout)

        ramp_layout = QHBoxLayout()
        self.lbl_ramp_speed = QLabel()
        ramp_layout.addWidget(self.lbl_ramp_speed)
        self.spin_ramp = QDoubleSpinBox()
        self.spin_ramp.setRange(0.001, 10.0)
        self.spin_ramp.setValue(1.0)
        self.spin_ramp.setDecimals(3)
        self.spin_ramp.setSingleStep(1.0)
        ramp_layout.addWidget(self.spin_ramp)
        self.btn_set_ramp = QPushButton()
        self.btn_set_ramp.clicked.connect(self._set_ramp_speed)
        ramp_layout.addWidget(self.btn_set_ramp)
        tcm_layout.addLayout(ramp_layout)

        hold_layout = QHBoxLayout()
        self.lbl_hold_time = QLabel()
        hold_layout.addWidget(self.lbl_hold_time)
        self.spin_hold = QSpinBox()
        self.spin_hold.setRange(0, 3600)
        self.spin_hold.setValue(60)
        hold_layout.addWidget(self.spin_hold)
        self.btn_start_profile = QPushButton()
        self.btn_start_profile.clicked.connect(self._start_temp_profile)
        hold_layout.addWidget(self.btn_start_profile)
        self.btn_stop_profile = QPushButton()
        self.btn_stop_profile.clicked.connect(self._stop_temp_profile)
        self.btn_stop_profile.setEnabled(False)
        hold_layout.addWidget(self.btn_stop_profile)
        tcm_layout.addLayout(hold_layout)

        out_layout = QHBoxLayout()
        self.chk_sw = QCheckBox()
        self.chk_sw.stateChanged.connect(self._toggle_sw)
        out_layout.addWidget(self.chk_sw)
        self.chk_oe = QCheckBox()
        self.chk_oe.stateChanged.connect(self._toggle_oe)
        out_layout.addWidget(self.chk_oe)
        tcm_layout.addLayout(out_layout)

        self.btn_emergency = QPushButton()
        self.btn_emergency.setStyleSheet("background-color: #ff4444; color: white; font-weight: bold;")
        self.btn_emergency.clicked.connect(self._emergency_stop)
        tcm_layout.addWidget(self.btn_emergency)

        self.lbl_actual_temp = QLabel()
        self.lbl_actual_temp.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        tcm_layout.addWidget(self.lbl_actual_temp)
        self.lbl_set_temp = QLabel()
        tcm_layout.addWidget(self.lbl_set_temp)
        self.lbl_actual_v = QLabel()
        tcm_layout.addWidget(self.lbl_actual_v)
        self.lbl_actual_i = QLabel()
        tcm_layout.addWidget(self.lbl_actual_i)
        self.lbl_info = QLabel()
        tcm_layout.addWidget(self.lbl_info)

        self.tcm_group.setLayout(tcm_layout)
        left_layout.addWidget(self.tcm_group)

        # --- Keithley 6487 ---
        self.keithley_group = QGroupBox()
        keithley_layout = QVBoxLayout()
        keithley_layout.setSpacing(4)

        conn_k_layout = QHBoxLayout()
        self.btn_connect_keithley = QPushButton()
        self.btn_connect_keithley.setCheckable(True)
        self.btn_connect_keithley.clicked.connect(self._toggle_keithley_connection)
        conn_k_layout.addWidget(self.btn_connect_keithley)
        self.lbl_keithley_status = QLabel()
        self.lbl_keithley_status.setStyleSheet("color: red; font-weight: bold;")
        conn_k_layout.addWidget(self.lbl_keithley_status)
        keithley_layout.addLayout(conn_k_layout)

        addr_layout = QHBoxLayout()
        self.lbl_gpib = QLabel()
        addr_layout.addWidget(self.lbl_gpib)
        self.edit_gpib = QLineEdit(KEITHLEY_VISA_ADDRESS)
        addr_layout.addWidget(self.edit_gpib)
        keithley_layout.addLayout(addr_layout)

        meas_layout = QHBoxLayout()
        self.lbl_meas_count = QLabel()
        meas_layout.addWidget(self.lbl_meas_count)
        self.spin_meas_count = QSpinBox()
        self.spin_meas_count.setRange(1, 10000)
        self.spin_meas_count.setValue(1)
        meas_layout.addWidget(self.spin_meas_count)
        self.btn_meas_once = QPushButton(tr("btn.measure_once"))
        self.btn_meas_once.clicked.connect(self._measure_keithley_once)
        meas_layout.addWidget(self.btn_meas_once)
        keithley_layout.addLayout(meas_layout)

        self.lbl_keithley_current = QLabel()
        self.lbl_keithley_current.setFont(QFont("Consolas", 11, QFont.Weight.Bold))
        keithley_layout.addWidget(self.lbl_keithley_current)
        self.lbl_keithley_error = QLabel()
        keithley_layout.addWidget(self.lbl_keithley_error)

        err_check_layout = QHBoxLayout()
        self.lbl_keithley_errors = QLabel()
        self.lbl_keithley_errors.setStyleSheet("color: #8a1f1f; font-size: 10px;")
        self.lbl_keithley_errors.setWordWrap(True)
        err_check_layout.addWidget(self.lbl_keithley_errors)

        self.chk_show_samples = QCheckBox(tr("chk.show_samples"))
        self.chk_show_samples.setChecked(False)
        err_check_layout.addWidget(self.chk_show_samples)
        err_check_layout.addStretch()

        keithley_layout.addLayout(err_check_layout)

        meas_ctrl_layout = QHBoxLayout()
        self.btn_start_meas = QPushButton()
        self.btn_start_meas.setCheckable(True)
        self.btn_start_meas.clicked.connect(self._toggle_continuous_measurement)
        meas_ctrl_layout.addWidget(self.btn_start_meas)

        self.lbl_export_name = QLabel()
        meas_ctrl_layout.addWidget(self.lbl_export_name)
        self.edit_export_name = QLineEdit(EXPORT_FILENAME)
        self.edit_export_name.setPlaceholderText(tr("placeholder.export_name"))
        meas_ctrl_layout.addWidget(self.edit_export_name)

        keithley_layout.addLayout(meas_ctrl_layout)

        self.keithley_group.setLayout(keithley_layout)
        left_layout.addWidget(self.keithley_group)

        # --- Current samples plot (left panel) ---
        self.plot_samples = pg.PlotWidget()
        self.plot_samples.showGrid(x=True, y=True)
        self.plot_samples.setMaximumWidth(420)
        self.plot_samples.setLabel("left", "I − ⟨I⟩", "A")
        # Keep references, so we can clear and redraw stems / outliers
        self._stem_lines: list = []
        self._scatter_items: list = []
        left_layout.addWidget(self.plot_samples)

        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # === Right panel: graphs ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget()

        self.plot_temp_time = pg.PlotWidget()
        self.plot_temp_time.showGrid(x=True, y=True)
        self.curve_temp = self.plot_temp_time.plot(pen=pg.mkPen(themes.PLOT_COLOR_TEMP, width=1.7))
        self.tabs.addTab(self.plot_temp_time, "T vs t")

        self.plot_current_time = pg.PlotWidget()
        self.plot_current_time.showGrid(x=True, y=True)
        self.curve_current = self.plot_current_time.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT, width=1.7))
        self.tabs.addTab(self.plot_current_time, "I vs t")

        self.plot_current_temp = pg.PlotWidget()
        self.plot_current_temp.showGrid(x=True, y=True)
        self.curve_current_temp = self.plot_current_temp.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT_TEMP, width=1.7))
        self.tabs.addTab(self.plot_current_temp, "I vs T")

        # --- Fourth tab: overview + reserved for future graph ---
        self.all_tab = QWidget()
        all_layout = QGridLayout(self.all_tab)

        self.plot_temp_all = pg.PlotWidget()
        self.plot_temp_all.showGrid(x=True, y=True)
        self.curve_temp_all = self.plot_temp_all.plot(pen=pg.mkPen(themes.PLOT_COLOR_TEMP, width=1.2))
        all_layout.addWidget(self.plot_temp_all, 0, 0)

        self.plot_current_time_all = pg.PlotWidget()
        self.plot_current_time_all.showGrid(x=True, y=True)
        self.curve_current_all = self.plot_current_time_all.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT, width=1.2))
        all_layout.addWidget(self.plot_current_time_all, 0, 1)

        self.plot_current_temp_all = pg.PlotWidget()
        self.plot_current_temp_all.showGrid(x=True, y=True)
        self.curve_current_temp_all = self.plot_current_temp_all.plot(
            symbol="o", 
            symbolSize=6, 
            symbolBrush=themes.PLOT_COLOR_CURRENT_TEMP,
            symbolPen=None,
            pen=None 
        )
        all_layout.addWidget(self.plot_current_temp_all, 1, 0)

        self.plot_future = pg.PlotWidget()
        self.plot_future.showGrid(x=True, y=True)
        self.curve_future = self.plot_future.plot(pen=pg.mkPen(themes.PLOT_COLOR_FUTURE, width=2))
        all_layout.addWidget(self.plot_future, 1, 1)

        self.tabs.addTab(self.all_tab, tr("tab.all"))

        right_layout.addWidget(self.tabs)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)

        self._gui_timer = QTimer()
        self._gui_timer.timeout.connect(self._poll_gui)
        self._gui_timer.start(UPDATE_INTERVAL_MS)

        self._measure_timer = QTimer(self)
        self._measure_timer.timeout.connect(self._request_measurement)

    def _apply_settings_to_ui(self) -> None:
        """Apply saved settings to UI widgets.

        Called after _build_ui(), but before _fill_lang_combo / _fill_theme_combo,
        so comboboxes reflect saved language and theme.
        """
        # Language – must be set before updating texts and comboboxes
        lang = self.settings.get("language", "en")
        i18n.set_language(lang)

        # Theme
        theme = self.settings.get("theme", "system")
        self._current_theme = theme
        themes.apply_theme(QApplication.instance(), theme)

        # TCM port
        self.edit_port.setText(self.settings.get("tcm_port", "COM10"))

        # Keithley GPIB address
        self.edit_gpib.setText(self.settings.get("keithley_visa_address", "GPIB0::22::INSTR"))

        # Export filename NOT saved in settings (generated with date at start)

        # Temperature profile
        self.spin_target_temp.setValue(float(self.settings.get("target_temp", 25.0)))
        self.spin_ramp.setValue(float(self.settings.get("ramp_speed", 1.0)))
        self.spin_hold.setValue(int(self.settings.get("hold_time", 60)))

        # Measurement count
        self.spin_meas_count.setValue(int(self.settings.get("meas_count", 1)))

        # Window size
        width = self.settings.get("window_width", 1400)
        height = self.settings.get("window_height", 900)
        self.resize(width, height)

        # Current samples plot – restored from settings (saved between runs)
        self.chk_show_samples.setChecked(bool(self.settings.get("show_samples", False)))
        self._show_samples_desired = self.chk_show_samples.isChecked()
        self._update_samples_plot_visibility()

    def _connect_settings_signals(self) -> None:
        """Connect autosave of settings when widgets change."""
        self.edit_port.editingFinished.connect(lambda: self.settings.set("tcm_port", self.edit_port.text()))
        self.edit_gpib.editingFinished.connect(
            lambda: self.settings.set("keithley_visa_address", self.edit_gpib.text())
        )
        # Export filename NOT saved (generated with date at start)

        # Temperature profile – save on every value change
        self.spin_target_temp.valueChanged.connect(lambda v: self.settings.set("target_temp", v))
        self.spin_ramp.valueChanged.connect(lambda v: self.settings.set("ramp_speed", v))
        self.spin_hold.valueChanged.connect(lambda v: self.settings.set("hold_time", v))

        # Measurement count
        self.spin_meas_count.valueChanged.connect(lambda v: self.settings.set("meas_count", v))

        # Show samples plot – saved between runs
        self.chk_show_samples.stateChanged.connect(self._on_show_samples_changed)

        # Language and theme are saved in their own change handlers
        # (_on_language_changed, _on_theme_changed).

    def _on_show_samples_changed(self, state: int) -> None:
        """Save the 'show samples plot' choice and refresh plot visibility.

        The choice is persisted immediately, so it survives between
        measurements and program restarts.
        """
        self._show_samples_desired = bool(state)
        self.settings.set("show_samples", self._show_samples_desired)
        self._update_samples_plot_visibility()

    def _update_samples_plot_visibility(self) -> None:
        """Show the samples plot only if enabled and the window is tall enough.

        Below SAMPLES_PLOT_MIN_HEIGHT the plot is hidden automatically, so it
        does not prevent the window from being resized to its minimum height.
        """
        show = self._show_samples_desired and self.height() >= SAMPLES_PLOT_MIN_HEIGHT
        self.plot_samples.setVisible(show)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_samples_plot_visibility()

    def _apply_styles(self) -> None:
        pass  # widget colors and rendering depend entirely on QStyleHints.setColorScheme()

    def _toggle_tcm_connection(self) -> None:
        if self.btn_connect_tcm.isChecked():
            try:
                self.tcm = TCMX107(
                    port=self.edit_port.text(),
                    baudrate=TCM_BAUDRATE,
                    timeout=TCM_TIMEOUT,
                    write_timeout=TCM_WRITE_TIMEOUT,
                )
                self.tcm.connect()
                self._tcm_model = self.tcm.get_model()
                self._retranslate_ui()
                self.statusBar.showMessage(tr("msg.tcm_connected").format(port=self.edit_port.text()))
                self.controller = TemperatureController(self.tcm)
                self.controller.set_update_callback(self._on_profile_update)
                # Software-based checkbox synchronized with the device's actual state
                try:
                    self.chk_sw.blockSignals(True)
                    self.chk_sw.setChecked(bool(self.tcm.get_sw()))
                    self.chk_sw.blockSignals(False)
                except Exception:
                    self.chk_sw.blockSignals(False)
                    pass
            except Exception as exc:
                self.btn_connect_tcm.setChecked(False)
                # Connection failed: do not keep a half-opened serial port
                # (it would block the next attempt) and do not show "connected"
                # by mistake in the status label.
                if self.tcm is not None:
                    try:
                        self.tcm.disconnect()
                    except Exception:
                        pass
                    self.tcm = None
                self._tcm_model = ""
                self._retranslate_ui()
                QMessageBox.critical(self, tr("err.title_tcm"), str(exc))
        else:
            if self.controller is not None:
                self.controller.stop_profile()
                self.controller = None
            if self.tcm is not None:
                try:
                    self.tcm.disconnect()
                except Exception:
                    pass
                self.tcm = None
            self._tcm_model = ""
            self._retranslate_ui()
            self.statusBar.showMessage(tr("status.ready"))

    def _set_target_temp(self) -> None:
        if self.tcm is None:
            return
        val = self.spin_target_temp.value()
        if not (SAFETY_MIN_TEMP <= val <= SAFETY_MAX_TEMP):
            QMessageBox.warning(self, tr("err.title_safety"),
                                tr("err.temp_range").format(min=SAFETY_MIN_TEMP, max=SAFETY_MAX_TEMP))
            return
        try:
            self.tcm.set_adjust_temp(val)
            self.statusBar.showMessage(f"{tr('lbl.set_temp')}: {val} °C")
        except Exception as exc:
            QMessageBox.critical(self, tr("err.generic"), str(exc))

    def _set_ramp_speed(self) -> None:
        if self.tcm is None:
            return
        try:
            self.tcm.set_ramp_speed(self.spin_ramp.value())
            self.statusBar.showMessage(
                f"{tr('label.ramp_speed')} {self.spin_ramp.value()} °C/с")
        except Exception as exc:
            QMessageBox.critical(self, tr("err.generic"), str(exc))

    def _start_temp_profile(self) -> None:
        if self.controller is None or self.tcm is None:
            return
        target = self.spin_target_temp.value()
        hold = self.spin_hold.value()
        ramp = self.spin_ramp.value()
        self.controller.start_profile(target_temp=target, hold_time_s=hold, ramp_speed=ramp)
        self.btn_start_profile.setEnabled(False)
        self.btn_stop_profile.setEnabled(True)
        self.statusBar.showMessage(tr("msg.profile").format(target=target, hold=hold))

    def _stop_temp_profile(self) -> None:
        if self.controller is not None:
            self.controller.stop_profile()
        self.btn_start_profile.setEnabled(True)
        self.btn_stop_profile.setEnabled(False)
        self.statusBar.showMessage(tr("msg.profile_stopped"))

    def _on_profile_update(self, actual: float, set_val: float, timestamp: float) -> None:
        self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: {actual:.2f} °C")  # noqa: E231
        self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: {set_val:.2f} °C")  # noqa: E231

    def _toggle_sw(self, state) -> None:
        if self.tcm is None:
            return
        self.tcm.set_sw(1 if state else 0)

    def _toggle_oe(self, state) -> None:
        if self.tcm is None:
            return
        self.tcm.set_oe(1 if state else 0)

    def _emergency_stop(self) -> None:
        if self.tcm is not None:
            self.tcm.emergency_stop()
        if self.controller is not None:
            self.controller.stop_profile()
        self.btn_start_profile.setEnabled(True)
        self.btn_stop_profile.setEnabled(False)
        self.chk_sw.setChecked(False)
        self.chk_oe.setChecked(False)
        self.statusBar.showMessage(tr("msg.emergency_stop"))
        QMessageBox.warning(self, tr("dlg.emergency_title"), tr("dlg.emergency_text"))

    def _toggle_keithley_connection(self) -> None:
        if self.btn_connect_keithley.isChecked():
            try:
                self.keithley = Keithley6487(visa_address=self.edit_gpib.text(), timeout_ms=KEITHLEY_TIMEOUT_MS)
                self.keithley.connect()
                self._keithley_ident = self.keithley.identity()
                self._retranslate_ui()
                self.statusBar.showMessage(tr("msg.keithley_connected").format(address=self.edit_gpib.text()))
            except Exception as exc:
                self.btn_connect_keithley.setChecked(False)
                # Same cleanup as for TCM: avoid a half-opened instrument object
                # and a wrong "connected" status label.
                if self.keithley is not None:
                    try:
                        self.keithley.disconnect()
                    except Exception:
                        pass
                    self.keithley = None
                self._keithley_ident = ""
                self._retranslate_ui()
                QMessageBox.critical(self, tr("err.title_keithley"), str(exc))
        else:
            if self.keithley is not None:
                try:
                    self.keithley.disconnect()
                except Exception:
                    pass
                self.keithley = None
            self._keithley_ident = ""
            self._retranslate_ui()
            self.statusBar.showMessage(tr("msg.keithley_disconnected"))

    def _measure_keithley_once(self) -> None:
        if self.keithley is None:
            QMessageBox.warning(self, tr("dlg.keithley"), tr("err.not_connected_keithley"))
            return
        try:
            count = self.spin_meas_count.value()
            self.keithley.configure_buffer(count)
            currents = self.keithley.read_buffer()
            curr, curr_err, outlier_indices = robust_average_current(currents)
            self.lbl_keithley_current.setText(f"{tr('lbl.current')}: {curr: .6e} A")
            self.lbl_keithley_error.setText(f"{tr('lbl.error')}: {curr_err: .6e} A")
            self.statusBar.showMessage(tr("msg.keithley_buffer").format(count=len(currents)))
            # Draw raw samples on the left-panel plot (residuals + outliers)
            self._update_samples_plot(currents, curr, curr_err, list(outlier_indices))
        except Exception as exc:
            QMessageBox.critical(self, tr("err.title_measure"), str(exc))
        self._show_keithley_errors()

    def _show_keithley_errors(self) -> None:
        if self.keithley is not None and self.keithley.last_errors:
            self.lbl_keithley_errors.setText("Keithley SCPI: " + " | ".join(self.keithley.last_errors))
        else:
            self.lbl_keithley_errors.clear()

    def _toggle_continuous_measurement(self, checked: bool) -> None:
        if checked:
            if self.tcm is None or not self.tcm.is_connected:
                QMessageBox.warning(self, tr("err.generic"), tr("err.connect_tcm_first"))
                self.btn_start_meas.setChecked(False)
                return
            raw_name = self.edit_export_name.text().strip()
            overwrite = False
            if raw_name and raw_name != EXPORT_FILENAME:
                # User-provided name: check for conflicts with existing files
                candidate = raw_name
                if not candidate.lower().endswith(".txt"):
                    candidate += ".txt"
                full_path = os.path.join(EXPORT_DIR, candidate)
                if os.path.exists(full_path):
                    # Custom dialog to guarantee button order:
                    # Continue -> Add suffix -> Overwrite -> Cancel
                    dlg = QDialog(self)
                    dlg.setWindowTitle(tr("dlg.file_exists"))
                    dlg_layout = QVBoxLayout(dlg)
                    dlg_layout.addWidget(QLabel(tr("dlg.file_exists_msg", name=candidate)))

                    btn_continue = QPushButton(tr("btn.continue"))
                    btn_keep = QPushButton(tr("btn.keep_both"))
                    btn_overwrite = QPushButton(tr("btn.overwrite"))
                    btn_cancel = QPushButton(tr("btn.cancel"))

                    dlg_layout.addWidget(btn_continue)
                    dlg_layout.addWidget(btn_keep)
                    dlg_layout.addWidget(btn_overwrite)
                    dlg_layout.addWidget(btn_cancel)

                    result = [0]  # 0 = cancel (default)

                    def _make_handler(code):
                        return lambda: (result.__setitem__(0, code), dlg.accept())

                    btn_continue.clicked.connect(_make_handler(1))
                    btn_keep.clicked.connect(_make_handler(2))
                    btn_overwrite.clicked.connect(_make_handler(3))
                    btn_cancel.clicked.connect(_make_handler(0))

                    dlg.exec()
                    choice = result[0]

                    if choice == 0:
                        self.btn_start_meas.setChecked(False)
                        return
                    if choice == 1:
                        # Append to the existing file without touching its contents
                        self.data_manager.continue_session(candidate)
                        self.statusBar.showMessage(tr("msg.meas_started"))
                        self._start_worker()
                        self.btn_start_meas.setText(tr("btn.stop_meas") if checked else tr("btn.start_meas"))
                        return
                    if choice == 2:
                        # Let DataManager pick the next free suffix (_1, _2...)
                        # "new_1" -> "new_2" instead of "new_1_1"
                        candidate = DataManager.get_available_filename(EXPORT_DIR, candidate)
                        # Keep the text field in sync with the chosen name
                        display_name = candidate
                        if display_name.lower().endswith(".txt"):
                            display_name = display_name[:-4]
                        self.edit_export_name.setText(display_name)
                    # choice == 3: overwrite -> keep candidate as-is
                    overwrite = (choice == 3)
                filename = candidate
            else:
                # Default name – add date to avoid overwriting files
                base_name = f"{EXPORT_FILENAME}_{datetime.now():%Y_%m_%d}"  # noqa: E231
                # If a measurement already exists for today, pick next free suffix
                filename = DataManager.get_available_filename(EXPORT_DIR, base_name)
            # Keithley's voltage: informational line in file header.
            # Not set from the program yet – read from instrument if connected.
            keithley_voltage = ""
            if self.keithley is not None and self.keithley.is_connected:
                try:
                    # Placeholder: Keithley 6487 voltage readback not implemented
                    # in the driver yet. Keep empty until a query method is added.
                    keithley_voltage = ""
                except Exception:
                    keithley_voltage = ""
            self.data_manager.start_session(
                filename,
                creation_time=time.time(),
                keithley_voltage=keithley_voltage,
                overwrite=overwrite,
            )
            self.statusBar.showMessage(tr("msg.meas_started"))
            self._start_worker()
        else:
            self._stop_worker()
            self.data_manager.end_session()
            self.statusBar.showMessage(tr("msg.meas_stopped"))
        self.btn_start_meas.setText(tr("btn.stop_meas") if checked else tr("btn.start_meas"))

    def _start_worker(self) -> None:
        """Create and start the measurement worker in a background thread."""
        if self._worker is not None:
            return
        self._worker = MeasurementWorker(self.tcm, self.keithley, self.data_manager)
        self._worker.set_buffer_count(self.spin_meas_count.value())

        # QThread already runs an event loop, so queued signals to the worker
        # are delivered across threads without a custom QThread subclass.
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker.data_ready.connect(self._on_data_ready)
        self._worker.samples_ready.connect(self._update_samples_plot)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.measure_requested.connect(self._worker.do_measurement)

        self._worker_thread.start()
        self._worker.start()  # sets _running = True
        self._measure_timer.start(UPDATE_INTERVAL_MS)
        # Block single-shot measure while continuous acquisition is running
        # to avoid GPIB access conflicts (VI_ERROR_TMO).
        self.btn_meas_once.setEnabled(False)

    def _stop_worker(self) -> None:
        """Stop the measurement worker thread."""
        self._measure_timer.stop()
        # Re-enable single-shot measure once continuous acquisition stops.
        self.btn_meas_once.setEnabled(True)
        if self._worker is not None:
            self._worker.stop()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait()
        self._worker = None
        self._worker_thread = None

    def _request_measurement(self) -> None:
        """Ask the worker to perform one measurement (runs in worker thread)."""
        if self._worker is not None and self.btn_start_meas.isChecked():
            self._worker.measure_requested.emit()

    def _on_data_ready(self, timestamp: float, temp: float, curr: float, curr_err: float) -> None:
        """Handle new measurement point from worker."""
        keithley_connected = (self.keithley is not None and self.keithley.is_connected)
        if keithley_connected:
            self.lbl_keithley_current.setText(f"{tr('lbl.current')}: {curr:.6e} A")  # noqa: E231
            self.lbl_keithley_error.setText(f"{tr('lbl.error')}: {curr_err:.6e} A")  # noqa: E231
        else:
            self.lbl_keithley_current.setText(f"{tr('lbl.current')}: -- A")
            self.lbl_keithley_error.setText(f"{tr('lbl.error')}: -- A")
        self._show_keithley_errors()
        # Keep the live temperature label fresh while measuring.
        self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: {temp:.2f} °C")  # noqa: E231
        # Redraw right-panel graphs immediately, so they don't lag behind _poll_gui ticks.
        self._redraw_right_plots()

    def _redraw_right_plots(self) -> None:
        """Redraw right-panel temperature/current graphs from stored points."""
        if not self.data_manager.points:
            return
        time_p, temp, cur, err = self.data_manager.get_arrays()
        if len(time_p) > PLOT_MAX_POINTS:
            time_p = time_p[-PLOT_MAX_POINTS:]
            temp = temp[-PLOT_MAX_POINTS:]
            cur = cur[-PLOT_MAX_POINTS:]
            err = err[-PLOT_MAX_POINTS:]
        # Temperature graph is always shown (TCM-X107 data).
        self.curve_temp.setData(time_p, temp)
        self.curve_temp_all.setData(time_p, temp)
        # Current graphs only when Keithley 6487 is connected.
        # When disconnected, zeros are still logged to file but not plotted.
        keithley_connected = (self.keithley is not None and self.keithley.is_connected)
        if keithley_connected:
            self.curve_current.setData(time_p, cur)
            self.curve_current_temp.setData(temp, cur)
            self.curve_current_all.setData(time_p, cur)
            self.curve_current_temp_all.setData(temp, cur)
        else:
            self.curve_current.setData([], [])
            self.curve_current_temp.setData([], [])
            self.curve_current_all.setData([], [])
            self.curve_current_temp_all.setData([], [])
        # Reserved for future graph – currently empty
        self.curve_future.setData([], [])

    def _update_samples_plot(self, currents, mean: float, error: float, outliers) -> None:
        """Update the left-panel current-samples plot.

        Draws vertical stem lines from zero for every residual (current − mean),
        highlights rejected outliers as red dots on their original sample indices.
        """
        if not self.plot_samples.isVisible():
            return

        # Clear previous stems and outliers
        for line in self._stem_lines:
            self.plot_samples.removeItem(line)
        self._stem_lines.clear()
        for item in self._scatter_items:
            self.plot_samples.removeItem(item)
        self._scatter_items.clear()

        n = len(currents) if currents is not None else 0
        if n == 0:
            return

        x = np.arange(n, dtype=float)
        residuals = np.array(currents, dtype=float) - mean

        outlier_set = set(outliers) if outliers else set()

        # Vertical stem lines from zero to each residual
        for i in range(n):
            xi = x[i]
            yi = float(residuals[i])
            if i in outlier_set:
                pen = pg.mkPen((220, 60, 60), width=2.0)   # outlier: red, thicker
            else:
                pen = pg.mkPen(themes.PLOT_COLOR_CURRENT, width=2.0)  # normal
            line = pg.PlotDataItem([xi, xi], [0.0, yi], pen=pen)
            self.plot_samples.addItem(line)
            self._stem_lines.append(line)

    def _on_worker_error(self, message: str) -> None:
        """Show worker error in status bar and stop measurements."""
        self.statusBar.showMessage(message, 5000)
        self.btn_start_meas.setChecked(False)
        self._stop_worker()

    def _poll_gui(self) -> None:
        # Read TCM status on every tick – these are short queries, not the full
        # Keithley acquisition, so they won't freeze the GUI noticeably.
        if self.tcm is not None and self.tcm.is_connected:
            try:
                temp = self.tcm.get_actual_temp()
                set_val = self.tcm.get_adjust_temp()
                voltage = self.tcm.get_actual_voltage()
                tcm_current = self.tcm.get_actual_current()
                info_raw = self.tcm.get_info()
                if "NOFAULT" in info_raw:
                    info_text = tr("lbl.info.no_fault")
                else:
                    info_text = tr("lbl.info.fault")
                self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: {temp:.2f} °C")  # noqa: E231
                self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: {set_val:.2f} °C")  # noqa: E231
                self.lbl_actual_v.setText(f"{tr('lbl.actual_voltage')}: {voltage:.3f} V")  # noqa: E231
                self.lbl_actual_i.setText(f"{tr('lbl.actual_current')}: {tcm_current:.3f} A")  # noqa: E231
                self.lbl_info.setText(f"{tr('lbl.info')}: {info_text}")
                if self.controller is not None and not self.controller.is_running():
                    self._on_profile_update(temp, set_val, 0.0)
            except Exception:
                pass

    def _fill_lang_combo(self) -> None:
        """Fill language combobox and set current language."""
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        for code, name in i18n.get_available_languages():
            self.lang_combo.addItem(name, code)
        idx = self.lang_combo.findData(i18n.get_language())
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        self.lang_combo.blockSignals(False)

    def _fill_theme_combo(self) -> None:
        """Fill theme combobox and set current theme."""
        self.theme_combo.blockSignals(True)
        self.theme_combo.clear()
        self.theme_combo.addItems([tr("theme.system"), tr("theme.light"), tr("theme.dark")])
        # Set index corresponding to current theme
        theme_map = {"system": 0, "light": 1, "dark": 2}
        idx = theme_map.get(self._current_theme, 0)
        self.theme_combo.setCurrentIndex(idx)
        self.theme_combo.blockSignals(False)

    def _on_theme_changed(self, index: int) -> None:
        theme_map = {0: "system", 1: "light", 2: "dark"}
        self._current_theme = theme_map.get(index, "system")
        self.settings.set("theme", self._current_theme)
        themes.apply_theme(QApplication.instance(), self._current_theme)
        # Force repaint all widgets with new color scheme
        app = QApplication.instance()
        for widget in app.allWidgets():
            app.style().unpolish(widget)
            app.style().polish(widget)
            widget.update()
        self.statusBar.showMessage(tr("status.settings_saved"))

    def _retranslate_ui(self) -> None:
        """Update all UI text elements for the current language."""
        self.setWindowTitle(tr("app.title"))
        self.lang_label.setText(tr("lang.label"))
        self.theme_label.setText(tr("theme.label"))

        # --- TCM-X107 ---
        self.tcm_group.setTitle(tr("tcm.group"))
        self.btn_connect_tcm.setText(
            tr("btn.disconnect_tcm") if self.btn_connect_tcm.isChecked()
            else tr("btn.connect_tcm")
        )
        if self.tcm is not None and self.tcm.is_connected:
            self.lbl_tcm_status.setText(tr("lbl.status.connected").format(model=self._tcm_model))
            self.lbl_tcm_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.lbl_tcm_status.setText(tr("lbl.status.disconnected"))
            self.lbl_tcm_status.setStyleSheet("color: #cc4444; font-weight: bold;")
        self.lbl_port.setText(tr("label.port"))
        self.lbl_target_temp.setText(tr("label.target_temp"))
        self.lbl_ramp_speed.setText(tr("label.ramp_speed"))
        self.lbl_hold_time.setText(tr("label.hold_time"))
        self.btn_set_temp.setText(tr("btn.set_temp"))
        self.btn_set_ramp.setText(tr("btn.set_ramp"))
        self.btn_start_profile.setText(tr("btn.start_profile"))
        self.btn_stop_profile.setText(tr("btn.stop_profile"))
        self.chk_sw.setText(tr("chk.sw"))
        self.chk_oe.setText(tr("chk.oe"))
        self.btn_emergency.setText(tr("btn.emergency"))
        self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: -- °C")
        self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: -- °C")
        self.lbl_actual_v.setText(f"{tr('lbl.actual_voltage')}: -- V")
        self.lbl_actual_i.setText(f"{tr('lbl.actual_current')}: -- A")
        self.lbl_info.setText(f"{tr('lbl.info')}: --")

        # --- Keithley 6487 ---
        self.keithley_group.setTitle(tr("keithley.group"))
        self.btn_connect_keithley.setText(
            tr("btn.disconnect_keithley") if self.btn_connect_keithley.isChecked()
            else tr("btn.connect_keithley")
        )
        if self.keithley is not None and self.keithley.is_connected:
            self.lbl_keithley_status.setText(tr("lbl.status.connected").format(model=self._keithley_ident))
            self.lbl_keithley_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.lbl_keithley_status.setText(tr("lbl.status.disconnected"))
            self.lbl_keithley_status.setStyleSheet("color: #cc4444; font-weight: bold;")
        self.lbl_gpib.setText(tr("label.gpib"))
        self.lbl_meas_count.setText(tr("label.meas_count"))
        self.btn_meas_once.setText(tr("btn.measure_once"))
        self.lbl_keithley_current.setText(f"{tr('lbl.current')}: -- A")
        self.lbl_keithley_error.setText(f"{tr('lbl.error')}: -- A")
        self.chk_show_samples.setText(tr('chk.show_samples'))
        self.btn_start_meas.setText(
            tr("btn.stop_meas") if self.btn_start_meas.isChecked()
            else tr("btn.start_meas")
        )
        self.lbl_export_name.setText(tr("label.export_name"))
        self.edit_export_name.setPlaceholderText(tr("placeholder.export_name"))

        # --- Graphs ---
        self.plot_temp_time.setTitle(tr("plot.temp_time.title"))
        self.plot_temp_time.setLabel("left", tr("axis.temperature"), "°C")
        self.plot_temp_time.setLabel("bottom", tr("axis.time"), tr("axis.time_unit"))
        self.plot_current_time.setTitle(tr("plot.current_time.title"))
        self.plot_current_time.setLabel("left", tr("axis.current"), "A")
        self.plot_current_time.setLabel("bottom", tr("axis.time"), tr("axis.time_unit"))
        self.plot_current_temp.setTitle(tr("plot.current_temp.title"))
        self.plot_current_temp.setLabel("left", tr("axis.current"), "A")
        self.plot_current_temp.setLabel("bottom", tr("axis.temperature"), "°C")
        self.tabs.setTabText(0, tr("tab.t_vs_t"))
        self.tabs.setTabText(1, tr("tab.i_vs_t"))
        self.tabs.setTabText(2, tr("tab.i_vs_T"))
        self.tabs.setTabText(3, tr("tab.all"))

        # --- Fourth tab: individual titles ---
        self.plot_temp_all.setTitle(tr("plot.temp_all.title"))
        self.plot_temp_all.setLabel("left", tr("axis.temperature"), "°C")
        self.plot_temp_all.setLabel("bottom", tr("axis.time"), tr("axis.time_unit"))
        self.plot_current_time_all.setTitle(tr("plot.current_time_all.title"))
        self.plot_current_time_all.setLabel("left", tr("axis.current"), "A")
        self.plot_current_time_all.setLabel("bottom", tr("axis.time"), tr("axis.time_unit"))
        self.plot_current_temp_all.setTitle(tr("plot.current_temp_all.title"))
        self.plot_current_temp_all.setLabel("left", tr("axis.current"), "A")
        self.plot_current_temp_all.setLabel("bottom", tr("axis.temperature"), "°C")
        self.plot_future.setTitle(tr("plot.future.title"))
        self.plot_future.setLabel("left", "", "")
        self.plot_future.setLabel("bottom", "", "")

        # --- Status bar ---
        if self.tcm is not None and self.tcm.is_connected:
            self.statusBar.showMessage(tr("msg.tcm_connected").format(port=self.edit_port.text()))
        elif self.keithley is not None and self.keithley.is_connected:
            self.statusBar.showMessage(tr("msg.keithley_connected").format(address=self.edit_gpib.text()))
        else:
            self.statusBar.showMessage(tr("status.ready"))

    def _on_language_changed(self, index: int) -> None:
        code = self.lang_combo.itemData(index)
        if code:
            i18n.set_language(code)
            self.settings.set("language", code)
            self._fill_lang_combo()
            # Do not reset theme – only update combobox texts
            self._fill_theme_combo()
            self._retranslate_ui()
            self.statusBar.showMessage(tr("status.settings_saved"))

    def closeEvent(self, event) -> None:
        # Save window size and all settings before exit
        self.settings.set("window_width", self.width(), autosave=False)
        self.settings.set("window_height", self.height(), autosave=False)
        self.settings.save()

        # Stop measurement worker first, so it doesn't keep accessing
        # the instruments while we disconnect them.
        self._stop_worker()

        if self.controller is not None:
            self.controller.stop_profile()
        if self.tcm is not None:
            try:
                self.tcm.disconnect()
            except Exception:
                pass
        if self.keithley is not None:
            try:
                self.keithley.disconnect()
            except Exception:
                pass
        event.accept()
