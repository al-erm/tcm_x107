"""
TCM-X107 temperature controller driver via virtual COM port (USB->RS-232).

Protocol (from communication dump):
  - each command ends with marker @0
  - query:   TC1:TCACTUALTEMP?      -> response: TC1:TCACTUALTEMP=24.06@0
  - write:   TC1:TCADJUSTTEMP=35    -> response: CMD:REPLY=1@0
  - all numbers use DOT as decimal separator
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import serial


class TCMX107Error(Exception):
    pass


class TCMX107:
    def __init__(self, port: str = "COM10", baudrate: int = 57600,
                 timeout: float = 1.0, write_timeout: float = 1.0) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._ser: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._connected = False

    # ------------------------------------------------------------------ #
    # Connect / disconnect
    # ------------------------------------------------------------------ #
    def connect(self) -> None:
        if self._connected:
            return
        try:
            self._ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.write_timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
            )
            time.sleep(0.5)
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            self._connected = True
        except serial.SerialException as exc:
            raise TCMX107Error(f"Failed to open port {self.port}: {exc}") from exc

    def disconnect(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.close()
                except serial.SerialException:
                    pass
                self._ser = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ #
    # Low-level communication
    # ------------------------------------------------------------------ #
    def _send(self, command: str) -> str:
        if not self._connected or self._ser is None:
            raise TCMX107Error("Instrument not connected")

        full = f"{command}@0\r"  # the character used as a line terminator is \r (CR), not \r\n (CRLF)
        data = full.encode("ascii", errors="ignore")

        with self._lock:
            try:
                self._ser.reset_output_buffer()
                self._ser.write(data)
                self._ser.flush()
                response = self._ser.read_until(b"@0", size=256)
            except serial.SerialException as exc:
                raise TCMX107Error(f"Communication error with instrument: {exc}") from exc

        if not response:
            raise TCMX107Error("Empty response from instrument (timeout)")

        text = response.decode("ascii", errors="ignore").strip()
        if text.endswith("@0"):
            text = text[:-2]
        text = text.strip("\r\n ")
        return text

    def query(self, command: str) -> str:
        text = self._send(command)
        if "=" in text:
            _, value = text.split("=", 1)
            return value.strip()
        return text.strip()

    def set(self, command: str) -> str:
        return self._send(command)

    # --- Temperature --------------------------------------------------- #
    def get_actual_temp(self) -> float:
        raw = self.query("TC1:TCACTUALTEMP?")
        return float(raw)

    def get_set_temp(self) -> float:
        raw = self.query("TC1:TCSETTEMP?")
        return float(raw)

    def set_adjust_temp(self, value: float) -> None:
        text = f"{value:.2f}".replace(",", ".")  # noqa: E231
        self.set(f"TC1:TCADJUSTTEMP={text}")  # noqa: E231

    def get_adjust_temp(self) -> float:
        raw = self.query("TC1:TCADJUSTTEMP?")
        return float(raw)

    # --- Temperature change speed ------------------------------------- #
    def get_ramp_speed(self) -> float:
        raw = self.query("TC1:TCRAMPSPEED?")
        return float(raw)

    def set_ramp_speed(self, value: float) -> None:
        text = f"{value:.6f}".replace(",", ".")  # noqa: E231
        self.set(f"TC1:TCRAMPSPEED={text}")  # noqa: E231

    # --- Temperature limits --------------------------------------------- #
    def get_max_set_temp(self) -> float:
        raw = self.query("TC1:TCMAXSETTEMP?")
        return float(raw)

    def get_min_set_temp(self) -> float:
        raw = self.query("TC1:TCMINSETTEMP?")
        return float(raw)

    def get_step_temp(self) -> float:
        raw = self.query("TC1:TCSTEPTEMP?")
        return float(raw)

    def get_decimal_digits(self) -> int:
        raw = self.query("TC1:TCDECIMALDIGITS?")
        return int(raw)

    # --- Voltage / current on TEC ------------------------------------- #
    def get_max_v(self) -> float:
        raw = self.query("TC1:TCMAXV?")
        return float(raw)

    def set_max_v(self, value: float) -> None:
        text = f"{value:.3f}".replace(",", ".")  # noqa: E231
        self.set(f"TC1:TCMAXV={text}")  # noqa: E231

    def get_cool_v_ratio(self) -> float:
        raw = self.query("TC1:TCCOOLVRATIO?")
        return float(raw)

    def set_cool_v_ratio(self, value: float) -> None:
        text = f"{value:.2f}".replace(",", ".")  # noqa: E231
        self.set(f"TC1:TCCOOLVRATIO={text}")  # noqa: E231

    def get_mode(self) -> int:
        raw = self.query("TC1:TCMODE?")
        return int(raw)

    def get_sw(self) -> int:
        raw = self.query("TC1:TCSW?")
        return int(raw)

    def set_sw(self, state: int) -> None:
        self.set(f"TC1:TCSW={int(state)}")  # noqa: E231

    def get_oe(self) -> int:
        raw = self.query("TC1:TCOE?")
        return int(raw)

    def set_oe(self, state: int) -> None:
        self.set(f"TC1:TCOE={int(state)}")  # noqa: E231

    def get_set_voltage(self) -> float:
        raw = self.query("TC1:TCSETVOLTAGE?")
        return float(raw)

    def get_actual_voltage(self) -> float:
        raw = self.query("TC1:TCACTUALVOLTAGE?")
        return float(raw)

    def get_actual_current(self) -> float:
        raw = self.query("TC1:TCACTUALCURRENT?")
        return float(raw)

    # --- Information / status ------------------------------------------ #
    def get_info(self) -> str:
        raw = self.query("MENU:INFO?")
        return raw

    def get_model(self) -> str:
        raw = self.query("SYSTEM:SYSMODEL?")
        return raw

    # --- Safety -------------------------------------------------- #
    def emergency_stop(self) -> None:
        try:
            self.set_sw(0)
            self.set_oe(0)
        except TCMX107Error:
            pass

    # ------------------------------------------------------------------ #
    # Context manager
    # ------------------------------------------------------------------ #
    def __enter__(self) -> "TCMX107":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()


# Import config at the end to avoid circular imports
try:
    from .. import config  # type: ignore
except ImportError:  # pragma: no cover
    import config  # type: ignore
