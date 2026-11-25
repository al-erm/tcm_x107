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

from config import (
        SOURCE_CURRENT_LIMIT_10V,
        SOURCE_CURRENT_LIMIT_50V_500V,
        SOURCE_VOLTAGE_RANGE_10,
        SOURCE_VOLTAGE_RANGE_50,
        SOURCE_VOLTAGE_RANGE_500,
    )  # noqa: E402


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
            idn = self.identity()
            self._check_errors()
            if not idn:
                raise Keithley6487Error("Keithley did not answer *IDN?")
            self._connected = True
            self.reset()
            time.sleep(0.5)
            self.perform_initial_zero_correction()
        except Exception as exc:
            self.disconnect()
            raise Keithley6487Error(f"Failed to connect to Keithley 6487 ({self.visa_address}): {exc}") from exc

    def disconnect(self) -> None:
        # Always close the VISA session, even if connect() failed partway.
        # Order matters: 0 V → output OFF → ZCH ON → GTL → close.
        # Each step is independent – none of them may abort the rest.
        if self._inst is not None:
            try:
                self.set_source_voltage_only(0.0)
            except Exception:
                pass
            time.sleep(0.1)
            try:
                self.set_source_output(False)
            except Exception:
                pass
            time.sleep(0.1)
            try:
                self.zero_check(True)
            except Exception:
                pass
            time.sleep(0.1)
            try:
                self._inst.control_ren(6)  # GTL (Go To Local)
            except Exception:
                pass
            try:
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

    # ------------------------------------------------------------------
    # Buffer measurements (N points at once: current)
    # ------------------------------------------------------------------

    def configure_buffer(self, count: int, nplc: float = 1.0) -> None:
        """Configure the instrument for N measurements.

        Sets NPLC, auto range and function.  The actual acquisition is done
        by ':TRAC:DATA?'.
        """
        if count < 1:
            raise Keithley6487Error("Buffer count must be >= 1")
        self.write(':DAMP 1')  # Enable analog filter damping
        self.set_nplc(nplc)
        self.write(':TRIG:DEL 0')
        self.auto_range(True)
        self.write(":FORM:ELEM READ")
        self.write(":TRAC:FEED:CONT NEV")
        self.write(f":TRIG:COUN {count}")  # noqa: E231
        self.write(f":TRAC:POIN {count}")  # noqa: E231
        self.write(":TRAC:FEED SENS")
        self.write(":TRAC:FEED:CONT NEXT")
        self._check_errors()
        self._buffer_count = count

    def read_buffer(self) -> list[float]:
        """Acquire the configured buffer and return [current_A]"""
        self.clear_errors()
        self.write(":TRAC:FEED:CONT NEV")  # clear the "storage active" lock
        self.write(":TRAC:CLE")  # clear buffer
        self.write(":TRAC:FEED:CONT NEXT")  # allow writing until the POIN is filled
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

    def measure_current_single(self) -> float:
        """Measure current once."""
        self.clear_errors()
        count = self.get_buffer_count()
        self.configure_buffer(1)
        raw = self.query(":READ?")
        errs = self._check_errors()
        self.configure_buffer(count)
        if errs:
            detail = "; ".join(errs)
            raise Keithley6487Error(f"Keithley SCPI errors during measure_current_single: {detail}")
        return float(raw)

    def get_buffer_count(self) -> int:
        """Return the currently configured buffer count."""
        return self._buffer_count

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

    def auto_zero(self) -> None:
        """Simulation of "ONCE" mode."""
        self.write(":SYST:AZER ON")
        self.measure_current_single()
        self.query("*OPC?")
        self.write(":SYST:AZER OFF")

    def perform_initial_zero_correction(self) -> None:
        """Performs a high-quality initial zero calibration (Zero Correct)."""
        self.clear_errors()
        try:
            self.write(":SYST:ZCH ON")
            self.write(":SYST:AZER ON")
            self.write(":INIT")
            self.query("*OPC?")
            self.write(":SYST:ZCOR:ACQ")
            self.write(":SYST:ZCOR ON")
            self.write(":SYST:AZER OFF")
            self.write(":SYST:ZCH OFF")
            self._check_errors()
        except Exception as exc:
            self.write(":SYST:ZCH OFF")
            raise Keithley6487Error(f"Ошибка при начальной калибровке нуля: {exc}")


    # ------------------------------------------------------------------
    # Built-in voltage source (Keithley 6487)
    # ------------------------------------------------------------------
    def set_source_voltage_range(self, range_v: float) -> None:
        """Set the voltage source range (:SOUR:VOLT:RANG), e.g. 10, 50 or 500 V."""
        self.write(f":SOUR:VOLT:RANG {float(range_v):g}")
        self._check_errors()

    def set_source_current_limit(self, limit_a: float) -> None:
        """Set the voltage source current limit (:SOUR:VOLT:ILIM) in Amperes."""
        self.write(f":SOUR:VOLT:ILIM {float(limit_a):g}")
        self._check_errors()

    def set_source_voltage(self, value: float) -> None:
        """Set the built-in voltage source level (V).

        Automatically selects the correct source range and current limit:
        - |V| <= 10 V     -> range 10 V, current limit 25 mA
        - 10 V < |V| <= 50 V -> range 50 V, current limit 2.5 mA
        - |V| > 50 V      -> range 500 V, current limit 2.5 mA
        """
        value = float(value)
        abs_v = abs(value)
        if abs_v > SOURCE_VOLTAGE_RANGE_500:
            raise Keithley6487Error(
                f"Source voltage {value} V exceeds Keithley 6487 limit "
                f"±{SOURCE_VOLTAGE_RANGE_500} V"
            )
        if abs_v <= SOURCE_VOLTAGE_RANGE_10:
            self.set_source_voltage_range(SOURCE_VOLTAGE_RANGE_10)
            self.set_source_current_limit(SOURCE_CURRENT_LIMIT_10V)
        elif abs_v <= SOURCE_VOLTAGE_RANGE_50:
            self.set_source_voltage_range(SOURCE_VOLTAGE_RANGE_50)
            self.set_source_current_limit(SOURCE_CURRENT_LIMIT_50V_500V)
        else:
            self.set_source_voltage_range(SOURCE_VOLTAGE_RANGE_500)
            self.set_source_current_limit(SOURCE_CURRENT_LIMIT_50V_500V)
        
        self.set_source_voltage_only(value)

    def set_source_voltage_only(self, value: float) -> None:
        """Set only the voltage source level (:SOUR:VOLT) without changing range/ILIM.

        Used during a smooth ramp where the range was already selected beforehand.
        """
        text = f"{float(value):.6f}"
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
