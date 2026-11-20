"""
Keithley 6487 picoammeter driver via GPIB (National Instruments PCIe-GPIB + NI-VISA).
Requires NI-VISA installed on the computer.

SCPI command reference (Keithley 6485 / 6487):
    *RST            — reset to defaults
    *IDN?           — identification
    :READ?          — single measurement, returns current in Amperes
    :SENS:CURR:NPLC <n>  — integration in power-line cycles (0.01 … 100)
    :SENS:CURR:RANG <val> — fixed current range (2e-9 … 2e-2 A)
    :SENS:CURR:RANG:AUTO ON|OFF — auto range
    :SYST:ZCH ON|OFF — zero check
    :SYST:ZERO:CORR ONCE — zero correct (once)
    SYST:ERR?       — read error queue (returns "0" when clear)

Overrange: Keithley returns 9.9E37 (or similar) when input exceeds range.
"""
from __future__ import annotations

import time

from typing import Optional

try:
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore


class Keithley6487Error(Exception):
    pass


class Keithley6487:
    # Keithley returns this when the measured current exceeds the selected range.
    OVERFLOW_MARKERS = ("9.9E37", "9.9E+37", "OVFL", "OVERFLOW")

    def __init__(self, visa_address: str = "GPIB0::22::INSTR", timeout_ms: int = 5000) -> None:
        self.visa_address = visa_address
        self.timeout_ms = timeout_ms
        self._rm = None
        self._inst = None
        self._connected = False
        self._nplc: float = 1.0
        self._current_range: Optional[float] = None
        self._buffer_count = 1
        self.last_errors: list[str] = []  # SCPI errors collected after commands

    def connect(self) -> None:
        if pyvisa is None:
            raise Keithley6487Error("pyvisa is not installed. Run: pip install pyvisa")
        if self._connected:
            return
        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self.visa_address)
            self._inst.timeout = self.timeout_ms
            self._inst.read_termination = "\n"
            self._inst.write_termination = "\n"
            self._inst.write("*CLS")
            self._check_errors()
            idn = self.identity
            self._check_errors()
            if not idn:
                raise Keithley6487Error("Keithley did not answer *IDN?")
            self._connected = True
            self.reset()
            time.sleep(1.0)
            self.zero_check(False)
        except Exception as exc:
            self.disconnect()
            raise Keithley6487Error(f"Failed to connect to Keithley 6487 ({self.visa_address}): {exc}") from exc

    def disconnect(self) -> None:
        self.zero_check(True)
        if self._inst is not None:
            try:
                # Go To Local: switch instrument to local mode before closing the GPIB session,
                # so the REMOTE/Talk indicator goes off right after disconnect.
                try:
                    self._inst.control_ren(6)  # GTL (Go To Local)
                except Exception:
                    pass
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        if self._rm is not None:
            try:
                self._rm.close()
            except Exception:
                pass
            self._rm = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def write(self, command: str) -> None:
        if not self._connected or self._inst is None:
            raise Keithley6487Error("Instrument not connected")
        self._inst.write(command)

    def query(self, command: str) -> str:
        if self._inst is None:
            raise Keithley6487Error("Instrument not connected")
        return self._inst.query(command).strip()

    # ------------------------------------------------------------------
    # Instrument setup
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset instrument to factory defaults (*RST)."""
        self.write("*RST")

    def identity(self) -> str:
        """Return instrument identification string (*IDN?)."""
        return self.query("*IDN?")

    def configure(
        self,
        nplc: float = 1.0,
        current_range: Optional[float] = None,
        auto_range: bool = True,
        zero_check: bool = False,
    ) -> None:
        """Configure the picoammeter for current measurement.

        Parameters
        ----------
        nplc : float
            Integration time in power-line cycles (e.g. 0.01, 1, 10).
        current_range : float | None
            Fixed range in Amperes (e.g. 2e-6). If None and auto_range is
            False, the instrument keeps its current range setting.
        auto_range : bool
            Enable automatic current ranging.
        zero_check : bool
            Enable ZERO CHECK before measuring. Usually OFF during measurement;
            enable only when you need to null input offset.
        """

        self.write(":FORM:ELEM READ")
        self.set_nplc(nplc)
        if auto_range:
            self.auto_range(True)
        elif current_range is not None:
            self.set_range(current_range)
        self.zero_check(zero_check)

    # ------------------------------------------------------------------
    # Buffer measurements (N points at once: time + current)
    # ------------------------------------------------------------------

    def configure_buffer(self, count: int, nplc: float = 1.0) -> None:
        """Configure the instrument for N measurements.

        Sets NPLC, auto range and function.  The actual acquisition is done
        by ':TRAC:DATA?'.
        """
        if count < 1:
            raise Keithley6487Error("Buffer count must be >= 1")
        self.write(':DAMP 1')
        self.write(f":SENS:CURR:NPLC {nplc}")  # noqa: E231
        self.write(':TRIG:DEL 0')
        self.auto_range(True)
        self.write(":FORM:ELEM READ")
        self.write(f":TRIG:COUN {count}")  # noqa: E231
        self.write(f":TRAC:POIN {count}")  # noqa: E231
        self.write(":TRAC:FEED SENS")
        self.write(":TRAC:FEED:CONT NEXT")
        self._check_errors()
        self._buffer_count = count

    def read_buffer(self) -> list[float]:
        """Acquire the configured buffer and return [current_A]"""
        self.clear_errors()
        self.write(":INIT")
        self.query("*OPC?")
        raw = self.query(":TRAC:DATA?")
        errs = self._check_errors()
        if errs:
            detail = "; ".join(errs)
            raise Keithley6487Error(f"Keithley SCPI errors during read_buffer: {detail}")

        points = [float(i) for i in raw.split(",")]

        if not points:
            raise Keithley6487Error(f"No data in buffer response: {raw!r}")
        return points

    def set_nplc(self, nplc: float) -> None:
        """Set integration time in power-line cycles."""
        self._nplc = float(nplc)
        self.write(f":SENS:CURR:NPLC {self._nplc}")  # noqa: E231

    def get_nplc(self) -> float:
        raw = self.query(":SENS:CURR:NPLC?")
        self._nplc = float(raw)
        return self._nplc

    def set_range(self, current_range: float) -> None:
        """Set fixed current range in Amperes (e.g. 2e-6)."""
        self._current_range = float(current_range)
        self.write(f":SENS:CURR:RANG {self._current_range}")  # noqa: E231

    def auto_range(self, enable: bool = True) -> None:
        """Enable/disable automatic current ranging."""
        self.write(f":SENS:CURR:RANG:AUTO {1 if enable else 0}")  # noqa: E231
        if enable:
            self._current_range = None

    def zero_check(self, enable: bool = True) -> None:
        """Enable/disable ZERO CHECK."""
        self.write(f":SYST:ZCH {1 if enable else 0}")  # noqa: E231

    def zero_correct(self) -> None:
        """Perform ZERO CORRECT once (:SYST:ZERO:CORR ONCE)."""
        self.write(":SYST:ZERO:CORR ONCE")

    # ------------------------------------------------------------------
    # Built-in voltage source (Keithley 6487)
    # ------------------------------------------------------------------
    def set_source_voltage(self, value: float) -> None:
        """Set the built-in voltage source level (V)."""
        text = f"{float(value):.6f}".replace(",", ".")
        self.write(f":SOUR:VOLT {text}")
        self._check_errors()

    def get_source_voltage(self) -> float:
        """Query the built-in voltage source level (V)."""
        raw = self.query(":SOUR:VOLT?")
        return float(raw)

    def set_source_output(self, state: bool) -> None:
        """Enable/disable the built-in voltage source output."""
        self.write(f":SOUR:VOLT:STAT {1 if state else 0}")
        self._check_errors()

    def get_source_output(self) -> bool:
        """Query the built-in voltage source output state."""
        raw = self.query(":SOUR:VOLT:STAT?")
        return raw.strip() in ("1", "ON")

    # ------------------------------------------------------------------
    # Instrument error queue
    # ------------------------------------------------------------------

    def clear_errors(self) -> None:
        """Clear the collected SCPI error list."""
        self.last_errors.clear()

    def _check_errors(self) -> list[str]:
        """Read and clear the instrument error queue (SYST:ERR?).

        Returns list of error strings. Does NOT raise — measurement must
        continue even if the error queue contains old entries.
        Collected errors are stored in self.last_errors for the GUI to show.
        """
        errors: list[str] = []
        if not self._connected or self._inst is None:
            return errors
        try:
            for _ in range(16):
                err = self.query("SYST:ERR?")
                parts = err.split(",", 1)
                try:
                    code = int(parts[0].strip())
                except (ValueError, IndexError):
                    break
                if code == 0:
                    break
                msg = parts[1].strip().strip('"') if len(parts) > 1 else err
                errors.append(f"Keithley error {code}: {msg}")
        except Exception:
            pass
        self.last_errors.extend(errors)
        return errors

    def __enter__(self) -> "Keithley6487":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
