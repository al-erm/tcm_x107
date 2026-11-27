"""
Temperature profile logic: target temperature, hold time, change speed.
Runs in a separate thread, controls TCM-X107.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from instruments.tcm_x107 import TCMX107  # type: ignore
from config import SAFETY_MIN_TEMP, SAFETY_MAX_TEMP


class TemperatureController:
    def __init__(self, instrument: TCMX107) -> None:
        self.instrument = instrument
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._running = False
        self._on_update: Optional[Callable[[float, float, float], None]] = None

    def set_update_callback(self, callback: Callable[[float, float, float], None]) -> None:
        self._on_update = callback

    def start_profile(
        self,
        target_temp: float,
        hold_time_s: float,
        ramp_speed: Optional[float] = None,
        tolerance: float = 0.5,
        poll_interval: float = 1.0,
    ) -> None:
        if self._running:
            return
        if not (SAFETY_MIN_TEMP <= target_temp <= SAFETY_MAX_TEMP):
            raise ValueError(
                f"Target temperature {target_temp}°C outside safety range "
                f"[{SAFETY_MIN_TEMP}, {SAFETY_MAX_TEMP}]"
            )
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_profile,
            args=(target_temp, hold_time_s, ramp_speed, tolerance, poll_interval),
            daemon=True,
        )
        self._thread.start()

    def stop_profile(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._running = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def is_running(self) -> bool:
        return self._running

    def _run_profile(
        self,
        target_temp: float,
        hold_time_s: float,
        ramp_speed: Optional[float],
        tolerance: float,
        poll_interval: float,
    ) -> None:
        try:
            if ramp_speed is not None:
                try:
                    self.instrument.set_ramp_speed(ramp_speed)
                except Exception:
                    pass

            self.instrument.set_adjust_temp(target_temp)
            self.instrument.set_sw(1)
            self.instrument.set_oe(1)

            while not self._stop_event.is_set():
                self._pause_event.wait()
                actual = self.instrument.get_actual_temp()
                set_val = self.instrument.get_adjust_temp()
                if self._on_update:
                    self._on_update(actual, set_val, time.time())
                if abs(actual - target_temp) <= tolerance:
                    break
                time.sleep(poll_interval)

            hold_start = time.time()
            while not self._stop_event.is_set():
                self._pause_event.wait()
                elapsed = time.time() - hold_start
                actual = self.instrument.get_actual_temp()
                set_val = self.instrument.get_adjust_temp()
                if self._on_update:
                    self._on_update(actual, set_val, time.time())
                if elapsed >= hold_time_s:
                    break
                time.sleep(poll_interval)

        finally:
            try:
                self.instrument.set_sw(0)
                self.instrument.set_oe(0)
            except Exception:
                pass
            self._running = False
