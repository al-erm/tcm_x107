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

from PyQt6.QtCore import QTimer, QObject, QThread, pyqtSignal, pyqtSlot, Qt  # noqa: E402

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter,
    QGroupBox, QLabel, QLineEdit, QPushButton, QDoubleSpinBox, QSpinBox, QCheckBox,
    QMessageBox, QDialog, QStatusBar, QTabWidget, QComboBox, QProgressBar,
)  # noqa: E402
from PyQt6.QtGui import QCloseEvent, QFont, QFontMetrics  # noqa: E402

import pyqtgraph as pg  # noqa: E402
import gui.i18n as i18n  # noqa: E402

from config import (
    TCM_PORT, TCM_BAUDRATE, TCM_TIMEOUT, TCM_WRITE_TIMEOUT,
    KEITHLEY_VISA_ADDRESS, KEITHLEY_TIMEOUT_MS,
    SAFETY_MIN_TEMP, SAFETY_MAX_TEMP, UPDATE_INTERVAL_MS, PLOT_MAX_POINTS,
    TEMP_STATUS_THRESHOLD, EXPORT_FILENAME, EXPORT_DIR,
    SOURCE_RAMP_DEFAULT_TIME, SOURCE_RAMP_DEFAULT_STEPS, SOURCE_VOLTAGE_RANGE_500
)  # noqa: E402

from instruments.tcm_x107 import TCMX107, TCMX107Error  # noqa: E402
from instruments.keithley_6487 import Keithley6487, Keithley6487Error  # noqa: E402
from core.controller import TemperatureController  # noqa: E402
from core.data_manager import DataManager, PlotDecimator  # noqa: E402
from core.math_processing import robust_average_current  # noqa: E402
from core.settings_manager import SettingsManager  # noqa: E402

from gui.i18n import tr  # noqa: E402
import gui.themes as themes  # noqa: E402


class MeasurementWorker(QObject):
    data_ready = pyqtSignal(float, float, float, float)
    samples_ready = pyqtSignal(object, float, float, object, float, float)
    error_occurred = pyqtSignal(str)
    measure_requested = pyqtSignal()

    def __init__(self, tcm: TCMX107, keithley: Keithley6487,
                 data_manager: DataManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.tcm = tcm
        self.keithley = keithley
        self.data_manager = data_manager
        self._running = False
        self._measure_interval = 0.5
        self._buffer_count = 1
        self._current_count = 1

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def set_interval(self, interval: float) -> None:
        self._measure_interval = max(0.1, interval)

    def set_buffer_count(self, count: int) -> None:
        """Number of points to acquire in one Keithley buffer."""
        self._buffer_count = max(1, count)
        self.keithley.configure_buffer(self._buffer_count)

    def set_current_count(self, count: int):
        """Tracks the current number of points in the buffer"""
        self._current_count = count

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
                currents = self.keithley.read_buffer()
                curr, curr_err, outlier_indices, slope, intercept = robust_average_current(currents)
                # Comparing buffer size and value with UI
                if self._current_count != self._buffer_count:
                    self.set_buffer_count(self._current_count)
            else:
                curr, curr_err = 0.0, 0.0
                currents = []
                outlier_indices = []
                slope, intercept = 0.0, 0.0

            status = self._get_temp_status(temp)

            self.data_manager.log_point(
                temperature=temp,
                current=curr,
                current_error=curr_err,
                status=status,
            )
            pt = self.data_manager.points[-1]
            self.data_ready.emit(pt.timestamp, temp, curr, curr_err)
            self.samples_ready.emit(currents, curr, curr_err, list(outlier_indices), slope, intercept)
        except Exception as exc:
            self.error_occurred.emit(str(exc))


class RampWorker(QObject):
    """Worker that performs a smooth voltage ramp in a background thread."""

    progress = pyqtSignal(int)
    voltage_changed = pyqtSignal(float)
    current_changed = pyqtSignal(float)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, keithley: Keithley6487, target_v: float,
                 algorithm: str, ramp_time: float, steps: int,
                 parent=None) -> None:
        super().__init__(parent)
        self.keithley = keithley
        self.target_v = float(target_v)
        self.algorithm = algorithm
        self.ramp_time = float(ramp_time)
        self.steps = max(1, int(steps))
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    @pyqtSlot()
    def run(self) -> None:
        try:
            current_v = 0.0
            # Setting the source voltage to zero and switching it on.
            self.keithley.set_source_voltage_only(current_v)
            time.sleep(0.1)
            self.keithley.set_source_output(True)
            time.sleep(0.1)
            for i in range(self.steps):
                if self._stop_requested:
                    break
                if self.algorithm == "linear":
                    next_v = self.target_v * (i + 1) / self.steps
                else:
                    if i == self.steps - 1:
                        next_v = self.target_v
                    else:
                        denom = 3.0 - i * (2.0 / max(self.steps - 1, 1))
                        remaining = self.target_v - current_v
                        next_v = current_v + remaining * (1.0 / denom)
                # Set only the level (range/ILIM already switched once by set_source_voltage).
                self.keithley.set_source_voltage_only(next_v)
                current_v = next_v
                self.voltage_changed.emit(current_v)
                # Wait part of the step time so the source settles before measuring.
                step_time = self.ramp_time / self.steps
                settle = step_time * 0.4
                t_end = time.time() + settle
                while time.time() < t_end:
                    if self._stop_requested:
                        break
                    time.sleep(0.05)
                if self._stop_requested:
                    break
                # Measure current once using :READ? (buffer = 1).
                try:
                    cur = self.keithley.measure_current_single()
                except Exception:
                    cur = float("nan")
                self.current_changed.emit(cur)
                # Wait the rest of the step time
                t_end = time.time() + max(step_time - settle, 0.05)
                while time.time() < t_end:
                    if self._stop_requested:
                        break
                    time.sleep(0.05)
                self.progress.emit(int((i + 1) / self.steps * 100))
        except Exception as exc:
            self.error.emit(str(exc))
            return
        try:
            if not self._stop_requested:
                self.keithley.set_source_voltage(self.target_v)
                self.voltage_changed.emit(self.target_v)
        except Exception:
            pass
        self.finished.emit()


class VoltageRampDialog(QDialog):
    """Modal dialog for smooth voltage ramp with progress bar."""

    rejected_signal = pyqtSignal()

    def __init__(self, keithley: Keithley6487, target_v: float, parent: MainWindow | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.ramp_title"))
        self.setModal(True)
        self.resize(420, 220)
        self.keithley = keithley
        self.target_v = float(target_v)
        self._worker: RampWorker | None = None
        self._worker_thread: QThread | None = None
        self._started = False

        layout = QVBoxLayout(self)

        alg_layout = QHBoxLayout()
        alg_layout.addWidget(QLabel(tr("lbl.ramp_algorithm")))
        self.combo_alg = QComboBox()
        self.combo_alg.addItems([tr("ramp.alg.linear"), tr("ramp.alg.nonlinear")])
        alg_layout.addWidget(self.combo_alg)
        alg_layout.addStretch()
        layout.addLayout(alg_layout)

        ts_layout = QHBoxLayout()
        self.spin_time = QDoubleSpinBox()
        self.spin_time.setRange(0.1, 3600.0)
        self.spin_time.setDecimals(1)
        self.spin_time.setValue(
            float(parent.settings.get("source_ramp_time", SOURCE_RAMP_DEFAULT_TIME))
            if parent is not None else SOURCE_RAMP_DEFAULT_TIME
        )
        ts_layout.addWidget(QLabel(tr("lbl.ramp_time")))
        ts_layout.addWidget(self.spin_time)
        self.spin_steps = QSpinBox()
        self.spin_steps.setRange(1, 1000)
        self.spin_steps.setValue(
            int(parent.settings.get("source_ramp_steps", SOURCE_RAMP_DEFAULT_STEPS))
            if parent is not None else SOURCE_RAMP_DEFAULT_STEPS
        )
        ts_layout.addWidget(QLabel(tr("lbl.ramp_steps")))
        ts_layout.addWidget(self.spin_steps)
        ts_layout.addStretch()
        layout.addLayout(ts_layout)

        layout.addWidget(QLabel(tr("lbl.ramp_progress")))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.lbl_voltage = QLabel(f"{tr('lbl.ramp_voltage')} 0.000 V")
        self.lbl_current = QLabel(f"{tr('lbl.ramp_current')} -- A")
        layout.addWidget(self.lbl_voltage)
        layout.addWidget(self.lbl_current)

        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton(tr("btn.ramp_start"))
        self.btn_start.clicked.connect(self._start_ramp)
        self.btn_close = QPushButton(tr("btn.cancel"))
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_close)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def _start_ramp(self) -> None:
        algorithm = "linear" if self.combo_alg.currentIndex() == 0 else "nonlinear"
        ramp_time = self.spin_time.value()
        steps = self.spin_steps.value()
        parent = self.parent()
        if parent is not None:
            parent.settings.set("source_ramp_time", ramp_time)
            parent.settings.set("source_ramp_steps", steps)
        self.combo_alg.setEnabled(False)
        self.spin_time.setEnabled(False)
        self.spin_steps.setEnabled(False)
        self.btn_start.setText(tr("btn.ramp_stop"))
        self.btn_start.clicked.disconnect()
        self.btn_start.clicked.connect(self._stop_ramp)
        self._original_buffer_count = self.keithley.get_buffer_count()
        self._worker = RampWorker(self.keithley, self.target_v, algorithm, ramp_time, steps)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.voltage_changed.connect(self._on_voltage_changed)
        self._worker.current_changed.connect(self._on_current_changed)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker_thread.started.connect(self._worker.run)
        self._worker_thread.start()
        self._started = True

    def _stop_ramp(self) -> None:
        """Stops the voltage increase at the current value and leaves the voltage switched on."""
        if self._worker is not None:
            self._worker.stop()
        self.btn_start.setEnabled(False)

    def _on_voltage_changed(self, v: float) -> None:
        self.lbl_voltage.setText(f"{tr('lbl.ramp_voltage')} {v:.3f} V")

    def _on_current_changed(self, cur: float) -> None:
        if cur != cur:
            self.lbl_current.setText(f"{tr('lbl.ramp_current')} -- A")
        else:
            self.lbl_current.setText(f"{tr('lbl.ramp_current')} {cur:.6e} A")

    def _on_finished(self) -> None:
        self._cleanup_thread()
        self.accept()

    def _on_error(self, msg: str) -> None:
        self._cleanup_thread()
        QMessageBox.critical(self, tr("err.title_measure"), msg)
        self.reject()

    def _cleanup_thread(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.quit()
            if not self._worker_thread.wait(3000):
                self._worker_thread.terminate()
                self._worker_thread.wait()
            self._worker_thread = None
        self._worker = None
        self.btn_start.setText(tr("btn.ramp_start"))
        self.btn_start.setEnabled(True)
        self.btn_start.clicked.disconnect()
        self.btn_start.clicked.connect(self._start_ramp)
        self.combo_alg.setEnabled(True)
        self.spin_time.setEnabled(True)
        self.spin_steps.setEnabled(True)

    def reject(self) -> None:
        if self._started and self._worker is not None:
            self._worker.stop()
            self._cleanup_thread()
        else:
            # Cancel picoammeter built-in source voltage ramp
            self.rejected_signal.emit()
        super().reject()


class MainWindow(QMainWindow):
    # Signal for thread-safe profile updates from the worker thread (TemperatureController)
    # Emitted from the worker thread, handled in the GUI thread via QueuedConnection.
    _profile_signal = pyqtSignal(float, float, float)

    def __init__(self) -> None:
        super().__init__()
        self.resize(1400, 900)

        self.tcm = None
        self.keithley = None
        self.controller = None
        self.data_manager = DataManager()
        self.decimator = PlotDecimator()

        self._tcm_model = ""
        self._keithley_ident = ""
        self._current_theme = "system"  # stores current theme for saving when language changes
        self._worker = None
        self._worker_thread = None
        self._gui_timer = None
        self._measure_timer = None
        self._profile_signal.connect(self._on_profile_update, Qt.ConnectionType.QueuedConnection)
        # Stem lines / outlier highlights for the current-samples plot (drawn on plot_samples)
        self._stem_lines: list = []
        self._scatter_items: list = []

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
        self.spin_target_temp.setRange(SAFETY_MIN_TEMP, SAFETY_MAX_TEMP)
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

        self.lbl_setpoint_temp = QLabel()
        tcm_layout.addWidget(self.lbl_setpoint_temp)
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

        source_voltage_layout = QHBoxLayout()
        self.lbl_source_voltage = QLabel()
        source_voltage_layout.addWidget(self.lbl_source_voltage)
        self.spin_source_voltage = QDoubleSpinBox()
        self.spin_source_voltage.setRange(-SOURCE_VOLTAGE_RANGE_500, SOURCE_VOLTAGE_RANGE_500)
        self.spin_source_voltage.setDecimals(1)
        self.spin_source_voltage.setSingleStep(0.1)
        self.spin_source_voltage.setValue(float(self.settings.get("source_voltage", 0.0)))
        self.spin_source_voltage.valueChanged.connect(lambda v: self.settings.set("source_voltage", float(v)))
        source_voltage_layout.addWidget(self.spin_source_voltage)
        self.btn_apply_voltage = QPushButton(tr("btn.apply_voltage"))
        self.btn_apply_voltage.setEnabled(False)
        self.btn_apply_voltage.clicked.connect(self._apply_source_voltage)
        source_voltage_layout.addWidget(self.btn_apply_voltage)
        keithley_layout.addLayout(source_voltage_layout)

        # Source output ON/OFF checkbox on its own line below the voltage setting.
        source_output_layout = QHBoxLayout()
        self.chk_source_output = QCheckBox()
        self.chk_source_output.stateChanged.connect(self._toggle_source_output)
        source_output_layout.addWidget(self.chk_source_output)
        # Smooth ramp checkbox (stored in settings, default OFF).
        self.chk_source_ramp = QCheckBox()
        self.chk_source_ramp.setChecked(bool(self.settings.get("source_ramp", False)))
        self.chk_source_ramp.stateChanged.connect(lambda s: self.settings.set("source_ramp", bool(s)))
        source_output_layout.addWidget(self.chk_source_ramp)
        source_output_layout.addStretch()
        keithley_layout.addLayout(source_output_layout)

        meas_layout = QHBoxLayout()
        self.lbl_meas_count = QLabel()
        meas_layout.addWidget(self.lbl_meas_count)
        self.spin_meas_count = QSpinBox()
        self.spin_meas_count.setRange(1, 10000)
        self.spin_meas_count.setValue(1)
        meas_layout.addWidget(self.spin_meas_count)
        self.btn_meas_once = QPushButton(tr("btn.measure_once"))
        self.btn_meas_once.setEnabled(False)
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

        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # === Right panel: graphs ===
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        self.tabs = QTabWidget()

        self.plot_temp_time = pg.PlotWidget()
        self.plot_temp_time.showGrid(x=True, y=True)
        self.curve_temp = self.plot_temp_time.plot(pen=pg.mkPen(themes.PLOT_COLOR_TEMP, width=1.7))
        self.curve_temp.setDownsampling(ds=True, auto=True, method='peak')
        self.tabs.addTab(self.plot_temp_time, "T vs t")

        self.plot_current_time = pg.PlotWidget()
        self.plot_current_time.showGrid(x=True, y=True)
        self.curve_current = self.plot_current_time.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT, width=1.7))
        self.curve_current.setDownsampling(ds=True, auto=True, method='peak')
        self.tabs.addTab(self.plot_current_time, "I vs t")

        self.plot_current_temp = pg.PlotWidget()
        self.plot_current_temp.showGrid(x=True, y=True)
        self.curve_current_temp = self.plot_current_temp.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT_TEMP, width=1.7))
        self.tabs.addTab(self.plot_current_temp, "I vs T")

        # --- Fourth tab ---
        self.all_tab = QWidget()
        all_layout = QGridLayout(self.all_tab)

        self.plot_temp_all = pg.PlotWidget()
        self.plot_temp_all.showGrid(x=True, y=True)
        self.curve_temp_all = self.plot_temp_all.plot(pen=pg.mkPen(themes.PLOT_COLOR_TEMP, width=1.2))
        self.curve_temp_all.setDownsampling(ds=True, auto=True, method='peak')
        all_layout.addWidget(self.plot_temp_all, 0, 0)

        self.plot_current_time_all = pg.PlotWidget()
        self.plot_current_time_all.showGrid(x=True, y=True)
        self.curve_current_all = self.plot_current_time_all.plot(pen=pg.mkPen(themes.PLOT_COLOR_CURRENT, width=1.2))
        self.curve_current_all.setDownsampling(ds=True, auto=True, method='peak')
        all_layout.addWidget(self.plot_current_time_all, 0, 1)

        self.plot_current_temp_all = pg.PlotWidget()
        self.plot_current_temp_all.showGrid(x=True, y=True)
        self.curve_current_temp_all = self.plot_current_temp_all.plot(
            symbol="o", 
            symbolSize=6, 
            symbolBrush=(*themes.PLOT_COLOR_CURRENT_TEMP, 200),
            symbolPen=None,
            pen=None 
        )
        all_layout.addWidget(self.plot_current_temp_all, 1, 0)

        self.plot_samples = pg.PlotWidget()
        self.plot_samples.showGrid(x=True, y=True)
        all_layout.addWidget(self.plot_samples, 1, 1)

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

        # Language and theme are saved in their own change handlers
        # (_on_language_changed, _on_theme_changed).

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

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
                self.controller.set_update_callback(
                    lambda actual, set_val, ts: self._profile_signal.emit(actual, set_val, ts)
                )
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
            self.statusBar.showMessage(f"{tr('label.ramp_speed')} {self.spin_ramp.value()}")
        except Exception as exc:
            QMessageBox.critical(self, tr("err.generic"), str(exc))

    def _start_temp_profile(self) -> None:
        if self.controller is None or self.tcm is None:
            return
        target = self.spin_target_temp.value()
        if not (SAFETY_MIN_TEMP <= target <= SAFETY_MAX_TEMP):
            QMessageBox.warning(self, tr("err.title_safety"),
                                tr("err.temp_range").format(min=SAFETY_MIN_TEMP, max=SAFETY_MAX_TEMP))
            return
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

    def _on_profile_update(self, actual: float, set_val: float) -> None:
        self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: {actual:.2f} °C")  # noqa: E231
        self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: {set_val:.2f} °C")  # noqa: E231
        if self.tcm is not None and self.tcm.is_connected:
            try:
                setpoint = self.tcm.get_set_temp()
                self.lbl_setpoint_temp.setText(
                    f"{tr('lbl.setpoint_temp')}: {setpoint:.2f} °C"  # noqa: E231
                )
            except Exception:
                pass

    def _toggle_sw(self, state) -> None:
        if self.tcm is None:
            return
        self.tcm.set_sw(1 if state else 0)

    def _toggle_oe(self, state) -> None:
        if self.tcm is None:
            return
        self.tcm.set_oe(1 if state else 0)

    def _emergency_stop(self) -> None:
        if self._worker is not None:
            self._stop_worker()
        if self.tcm is not None and self.tcm.is_connected:
            self.tcm.emergency_stop()
        if self.controller is not None:
            self.controller.stop_profile()
        keithley_off = True
        if self.keithley is not None and self.keithley.is_connected:
            try:
                self.keithley.set_source_output(False)
                self.chk_source_output.setChecked(False)
            except Exception:
                keithley_off = False
        self.btn_start_profile.setEnabled(True)
        self.btn_stop_profile.setEnabled(False)
        self.chk_sw.setChecked(False)
        self.chk_oe.setChecked(False)
        self.btn_start_meas.setChecked(False)
        self.btn_start_meas.setText(tr("btn.start_meas"))
        self.statusBar.showMessage(tr("msg.emergency_stop"))

        # Preparing a message to disable device outputs
        lines: list[str] = []
        if self.tcm is not None and self.tcm.is_connected:
            lines.append(tr("dlg.emergency_text"))
        if self.keithley is not None and self.keithley.is_connected:
            if keithley_off:
                lines.append(tr("dlg.emergency_keithley_off"))
            else:
                lines.append(tr("err.keithley_not_disabled"))
        if not lines:
            QMessageBox.warning(
                self, tr("dlg.emergency_title"), tr("dlg.emergency_nothing"))
        elif self.keithley is not None and self.keithley.is_connected and not keithley_off:
            QMessageBox.critical(
                self, tr("dlg.emergency_title"), "\n".join(lines))
        else:
            QMessageBox.warning(
                self, tr("dlg.emergency_title"), "\n".join(lines))

    def _toggle_keithley_connection(self) -> None:
        if self.btn_connect_keithley.isChecked():
            try:
                self.keithley = Keithley6487(visa_address=self.edit_gpib.text(), timeout_ms=KEITHLEY_TIMEOUT_MS)
                self.keithley.connect()
                self._keithley_ident = self.keithley.identity()
                self._retranslate_ui()
                self.btn_apply_voltage.setEnabled(True)
                self.btn_meas_once.setEnabled(True)
                self.chk_source_output.setChecked(False)
                # Apply the voltage value from the spin box to the instrument
                # (set level only, output stays off) and configure the buffer
                # for the selected measurement count — without starting a measurement.
                try:
                    self.keithley.set_source_voltage(self.spin_source_voltage.value())
                    self.keithley.configure_buffer(self.spin_meas_count.value())
                except Exception as exc:
                    self.statusBar.showMessage(f"Keithley setup: {exc}", 5000)
                self._show_keithley_errors()
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
                self.btn_apply_voltage.setEnabled(False)
                self.btn_meas_once.setEnabled(False)
                self.chk_source_output.setChecked(False)
                self._retranslate_ui()
                QMessageBox.critical(self, tr("err.title_keithley"), str(exc))
        else:
            if self.keithley is not None:
                try:
                    # Turn the voltage source output off before disconnecting.
                    self.keithley.set_source_output(False)
                except Exception:
                    pass
                try:
                    self.keithley.disconnect()
                except Exception:
                    pass
                self.keithley = None
            self._keithley_ident = ""
            self.btn_apply_voltage.setEnabled(False)
            self.btn_meas_once.setEnabled(False)
            self.chk_source_output.setChecked(False)
            self._retranslate_ui()
            self.statusBar.showMessage(tr("msg.keithley_disconnected"))

    def _apply_source_voltage(self) -> None:
        if self.keithley is None:
            return
        try:
            # The source output is enabled separately with the checkbox below
            # (:SOUR:VOLT:STAT). This button only sets the voltage level.
            self.keithley.set_source_voltage(self.spin_source_voltage.value())
        except Exception as exc:
            QMessageBox.critical(self, tr("err.title_measure"), str(exc))
            self._show_keithley_errors()
            return
        self._show_keithley_errors()
        if self.keithley.last_errors:
            QMessageBox.warning(
                self, tr("err.title_measure"),
                "\n".join(self.keithley.last_errors)
            )
            return
        self.statusBar.showMessage(tr("msg.source_voltage_set").format(voltage=self.spin_source_voltage.value()), 3000)

    def _toggle_source_output(self, state: bool) -> None:
        """Enable/disable the Keithley 6487 voltage source output (:SOUR:VOLT:STAT).

        When turning ON with the smooth-ramp checkbox checked, ramp the voltage
        smoothly from 0 to the currently set level (modal progress dialog).
        """
        if self.keithley is None:
            return
        try:
            if bool(state):
                if self.chk_source_ramp.isChecked():
                    dlg = VoltageRampDialog(self.keithley, self.spin_source_voltage.value(), self)
                    dlg.rejected.connect(self._on_ramp_dialog_rejected)
                    dlg.exec()
                else:
                    # Just set the level (range/ILIM switched once by set_source_voltage).
                    self.keithley.set_source_voltage(self.spin_source_voltage.value())
                    self.keithley.set_source_output(True)
            else:
                self.keithley.set_source_output(False)
        except Exception as exc:
            QMessageBox.critical(self, tr("err.title_measure"), str(exc))
        self._show_keithley_errors()

    def _on_ramp_dialog_rejected(self) -> None:
        """Handles the automatic cleanup when the VoltageRampDialog is canceled."""
        if self.chk_source_output.isChecked():
            self.chk_source_output.click()

    def _measure_keithley_once(self) -> None:
        if self.keithley is None:
            return
        try:
            self.keithley.configure_buffer(self.spin_meas_count.value())
            currents = self.keithley.read_buffer()
            curr, curr_err, outlier_indices, slope, intercept = robust_average_current(currents)
            self.lbl_keithley_current.setText(f"{tr('lbl.current')}: {curr: .6e} A")
            self.lbl_keithley_error.setText(f"{tr('lbl.error')}: {curr_err: .6e} A")
            self.statusBar.showMessage(tr("msg.keithley_buffer").format(count=len(currents)))
            # Draw raw samples on the samples plot (residuals from the fitted line + outliers)
            self._update_samples_plot(currents, curr, curr_err, list(outlier_indices), slope, intercept)
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
            # Read the actual source voltage from the instrument if connected.
            keithley_voltage = ""
            if self.keithley is not None and self.keithley.is_connected:
                try:
                    keithley_voltage = f"{self.keithley.get_source_voltage():.3f} V"
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
        # Pass the already connected instrument to DataManager before starting
        self.data_manager.keithley = self.keithley
        # Clear decimator so old points from a previous run do not stay on
        # the shared "All" tab when a new measurement starts.
        self.decimator.clear()

        self._worker = MeasurementWorker(self.tcm, self.keithley, self.data_manager)
        if self.keithley is not None and self.keithley.is_connected:
            # Set identical values
            self._worker.set_buffer_count(self.spin_meas_count.value())
            self._worker.set_current_count(self.spin_meas_count.value())
            # Binding the current UI value to _worker.set_current_count safely
            # across threads using a queue via QueuedConnection
            self.spin_meas_count.valueChanged.connect(
                self._worker.set_current_count,
                Qt.ConnectionType.QueuedConnection
            )

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
        # Also block the source voltage apply button and the output ON/OFF
        # checkbox while measuring continuously — changing STAT mid-acquisition
        # causes GPIB errors.
        self.btn_apply_voltage.setEnabled(False)
        self.chk_source_output.setEnabled(False)

    def _stop_worker(self) -> None:
        """Stop the measurement worker thread."""
        if self._worker is None:
            return
        if self._worker is not None:
            self._worker.stop()
        self._measure_timer.stop()
        # Re-enable single-shot measure once continuous acquisition stops,
        # but only if Keithley is actually connected.
        if self.keithley is not None and self.keithley.is_connected:
            self.keithley.clear()
            self.btn_meas_once.setEnabled(True)
            self.btn_apply_voltage.setEnabled(True)
            self.chk_source_output.setEnabled(True)
            try:
                self.spin_meas_count.valueChanged.disconnect(self._worker.set_current_count)
            except (TypeError, RuntimeError):
                pass
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

        # Parallel write to the shared-tab plot buffer with dynamic decimation.
        self.decimator.add_point(timestamp, temp, curr)

    def _redraw_right_plots(self) -> None:
        """Redraw right-panel temperature/current graphs from stored points.

        First three tabs draw from DataManager ring buffer. The shared
        "All" tab draws from PlotDecimator with dynamic decimation to avoid
        the accordion effect.
        """
        if not self.data_manager.points:
            return

        # --- First three tabs: DataManager (unchanged ring buffer) ---
        time_p, temp, cur = self.data_manager.get_arrays()
        if len(time_p) > PLOT_MAX_POINTS:
            time_p = time_p[-PLOT_MAX_POINTS:]
            temp = temp[-PLOT_MAX_POINTS:]
            cur = cur[-PLOT_MAX_POINTS:]

        # Temperature graph is always shown (TCM-X107 data).
        self.curve_temp.setData(time_p, temp)

        keithley_connected = (self.keithley is not None and self.keithley.is_connected)
        if keithley_connected:
            self.curve_current.setData(time_p, cur)
            self.curve_current_temp.setData(temp, cur)
        else:
            self.curve_current.setData([], [])
            self.curve_current_temp.setData([], [])

        # --- Fourth tab ("All"): PlotDecimator with dynamic decimation ---
        d_time, d_temp, d_cur = self.decimator.get_arrays()
        if d_time:
            self.curve_temp_all.setData(d_time, d_temp)
            if keithley_connected:
                self.curve_current_all.setData(d_time, d_cur)
                self.curve_current_temp_all.setData(d_temp, d_cur)
            else:
                self.curve_current_all.setData([], [])
                self.curve_current_temp_all.setData([], [])
        else:
            self.curve_temp_all.setData([], [])
            self.curve_current_all.setData([], [])
            self.curve_current_temp_all.setData([], [])

    def _update_samples_plot(
        self, currents, mean: float, error: float, outliers, slope: float = 0.0,
        intercept: float = 0.0,
    ) -> None:
        """Draw current samples on the samples plot.

        Draws vertical stem lines from zero for every residual (current − fitted
        line), where the fitted line is the Siegel regression used by
        ``robust_average_current`` for outlier rejection. Rejected outliers are
        highlighted in red on their original sample indices.

        Using residuals from the fitted line (not from a constant mean) keeps the
        plot consistent with the outlier detection: a slow drift is absorbed by
        the line, so only true deviations from the trend appear as large stems
        and are coloured red.
        """
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
        fitted = slope * x + intercept
        residuals = np.array(currents, dtype=float) - fitted

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
                setpoint = self.tcm.get_set_temp()
                set_val = self.tcm.get_adjust_temp()
                voltage = self.tcm.get_actual_voltage()
                tcm_current = self.tcm.get_actual_current()
                info_raw = self.tcm.get_info()
                if "NOFAULT" in info_raw:
                    info_text = tr("lbl.info.no_fault")
                else:
                    info_text = tr("lbl.info.fault")
                self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: {temp:.2f} °C")  # noqa: E231
                self.lbl_setpoint_temp.setText(f"{tr('lbl.setpoint_temp')}: {setpoint:.2f} °C")  # noqa: E231
                self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: {set_val:.2f} °C")  # noqa: E231
                self.lbl_actual_v.setText(f"{tr('lbl.actual_voltage')}: {voltage:.3f} V")  # noqa: E231
                self.lbl_actual_i.setText(f"{tr('lbl.actual_current')}: {tcm_current:.3f} A")  # noqa: E231
                self.lbl_info.setText(f"{tr('lbl.info')}: {info_text}")
                if self.controller is not None and not self.controller.is_running():
                    self._on_profile_update(temp, set_val)
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
        self.lbl_setpoint_temp.setText(f"{tr('lbl.setpoint_temp')}: -- °C")
        self.lbl_actual_temp.setText(f"{tr('lbl.actual_temp')}: -- °C")
        self.lbl_set_temp.setText(f"{tr('lbl.set_temp')}: -- °C")
        self.lbl_actual_v.setText(f"{tr('lbl.actual_voltage')}: -- V")
        self.lbl_actual_i.setText(f"{tr('lbl.actual_current')}: -- A")
        self.lbl_info.setText(f"{tr('lbl.info')}: --")
        self.lbl_source_voltage.setText(tr("lbl.source_voltage"))
        self.chk_source_output.setText(tr("chk.source_output"))
        self.chk_source_ramp.setText(tr("chk.source_ramp"))
        self.btn_apply_voltage.setText(tr("btn.apply_voltage"))

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

        self.btn_start_meas.setText(tr("btn.stop_meas") if self.btn_start_meas.isChecked() else tr("btn.start_meas"))
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
        self.tabs.setTabText(0, tr("tab.T_vs_t"))
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
        self.plot_samples.setTitle(tr("plot.samples.title"))
        self.plot_samples.setLabel("left", tr("axis.residuals"), "A")
        self.plot_samples.setLabel("bottom", tr("axis.sample"), "")

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

    def closeEvent(self, event: QCloseEvent) -> None:
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
