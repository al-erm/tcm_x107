"""
Data manager: writes each point to .txt immediately after measurement.
The file is opened and closed on every write to avoid data loss.
"""

from __future__ import annotations

import csv
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from config import EXPORT_DELIMITER, EXPORT_ENCODING, EXPORT_FILENAME, EXPORT_DIR, EXPORT_AUTO_CREATE_DIR


@dataclass
class MeasurementPoint:
    timestamp: float
    temperature: float
    current: float
    current_error: float
    status: int = 0


class DataManager:
    def __init__(self) -> None:
        self.points: List[MeasurementPoint] = []
        self._start_time: Optional[float] = None
        self._filename: Optional[str] = None
        self._header_written = False
        self._creation_time: Optional[float] = None
        self._keithley_voltage: str = ""

    def _resolve_path(self, filename: Optional[str] = None) -> str:
        """Return full path to file in data folder with .txt extension."""
        base_name = filename or EXPORT_FILENAME
        if not base_name.lower().endswith(".txt"):
            base_name += ".txt"
        if EXPORT_AUTO_CREATE_DIR and not os.path.isdir(EXPORT_DIR):
            os.makedirs(EXPORT_DIR, exist_ok=True)
        return os.path.join(EXPORT_DIR, base_name)

    @staticmethod
    def get_available_filename(directory: str, base_name: str) -> str:
        """Return a filename in *directory* that does not exist yet.

        If *base_name* already exists, appends ``_1``, ``_2``, ... before the
        extension.  An existing numeric suffix is stripped first so that
        ``new_1`` becomes ``new_2`` instead of ``new_1_1``.
        """
        if not base_name.lower().endswith(".txt"):
            base_name += ".txt"

        full_path = os.path.join(directory, base_name)
        if not os.path.exists(full_path):
            return base_name

        name, ext = os.path.splitext(base_name)

        # If this is a default-name file with a date suffix
        # do not strip the trailing date number.
        # Instead, append _1, _2 ... after the full date:
        # measurement_YYYY_MM_DD -> measurement_YYYY_MM_DD_1 -> _2 ...
        if name.startswith(EXPORT_FILENAME + "_"):
            base = name
        else:
            base = re.sub(r"_\d+$", "", name)
        counter = 1
        while True:
            candidate = f"{base}_{counter}{ext}"
            if not os.path.exists(os.path.join(directory, candidate)):
                return candidate
            counter += 1

    def start_session(
        self,
        filename: Optional[str] = None,
        creation_time: Optional[float] = None,
        keithley_voltage: str = "",
        overwrite: bool = False,
    ) -> None:
        """Start a new measurement series. File is created on first write.

        Existing files are never deleted by this class unless *overwrite* is
        True.  The caller (GUI) is responsible for choosing an unused name or
        confirming overwrite.
        """
        self.points.clear()
        self._start_time = time.time()
        self._filename = self._resolve_path(filename)

        # Safety: never silently truncate an existing file.
        if not overwrite and os.path.exists(self._filename):
            raise FileExistsError(
                f"Export file already exists: {self._filename}. "
                "Use overwrite=True or select a different name."
            )

        self._header_written = False
        self._creation_time = creation_time if creation_time is not None else self._start_time
        self._keithley_voltage = keithley_voltage

    def continue_session(self, filename: Optional[str] = None) -> None:
        """Resume writing to an existing file without overwriting it.

        The file is assumed to already contain a header; new points are
        appended to the end. Internal point list is reset for the graph.
        Timestamps continue from the last recorded value in the file so the
        time column stays monotonic.
        """
        self.points.clear()
        self._filename = self._resolve_path(filename)
        self._header_written = True

        # Continue the time axis from the last value already written to the
        # file, otherwise log_point() would restart at 0 and produce a
        # non-monotonic time column.
        last_time_s = self._last_recorded_time_seconds(self._filename)
        self._start_time = time.time() - last_time_s

    @staticmethod
    def _last_recorded_time_seconds(path: str) -> float:
        """Return the last time value (seconds) from an existing export file,
        or 0.0 if the file is missing, empty, or unreadable."""
        if not os.path.exists(path):
            return 0.0
        try:
            with open(path, "r", encoding=EXPORT_ENCODING, newline="") as f:
                lines = f.readlines()
        except OSError:
            return 0.0
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            parts = line.split(EXPORT_DELIMITER)
            try:
                t_min = float(parts[0])
                return t_min * 60.0
            except (ValueError, IndexError):
                continue
        return 0.0

    def log_point(
        self,
        temperature: float,
        current: float,
        current_error: float,
        status: int = 0,
    ) -> None:
        """Write one point immediately to file (open -> write -> close)."""
        if self._start_time is None:
            self.start_session()

        timestamp = time.time() - self._start_time
        point = MeasurementPoint(
            timestamp=timestamp,
            temperature=temperature,
            current=current,
            current_error=current_error,
            status=status,
        )
        self.points.append(point)

        mode = "a" if self._header_written else "w"
        with open(self._filename, mode, encoding=EXPORT_ENCODING, newline="") as f:
            writer = csv.writer(f, delimiter=EXPORT_DELIMITER)
            if not self._header_written:
                # Line 1: date and time of file creation
                creation_str = datetime.fromtimestamp(self._creation_time).strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow([creation_str])
                # Line 2: Keithley voltage (informational, not set from program)
                writer.writerow([f"Keithley voltage: {self._keithley_voltage}"])
                # Line 3: empty
                writer.writerow([])
                # Line 4: column headers (English)
                writer.writerow(
                    [
                        "time (min)",
                        "temperature (C)",
                        "current (A)",
                        "current_error (A)",
                        "status",
                    ]
                )
                self._header_written = True
            writer.writerow(
                [
                    f"{timestamp / 60:.3f}",  # noqa: E231
                    f"{temperature:.4f}",  # noqa: E231
                    f"{current:.6e}",  # noqa: E231
                    f"{current_error:.6e}",  # noqa: E231
                    f"{status}",
                ]
            )

    def end_session(self) -> None:
        """End the series (file is already closed, nothing more needed)."""
        pass

    def get_arrays(self):
        if not self.points:
            return [], [], [], []
        time_p = [p.timestamp for p in self.points]
        temp = [p.temperature for p in self.points]
        cur = [p.current for p in self.points]
        err = [p.current_error for p in self.points]
        return time_p, temp, cur, err
