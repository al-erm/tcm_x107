"""
Project configuration: ports, addresses, default values.
"""

import os

# --- TCM-X107 (USB -> RS-232 virtual COM port) ---
TCM_PORT = os.environ.get("TCM_PORT", "COM10")   # replace with your port
TCM_BAUDRATE = int(os.environ.get("TCM_BAUDRATE", "57600"))
TCM_TIMEOUT = float(os.environ.get("TCM_TIMEOUT", "1.0"))
TCM_WRITE_TIMEOUT = float(os.environ.get("TCM_WRITE_TIMEOUT", "1.0"))
TCM_CMD_TERMINATOR = "@0"

# --- Keithley 6487 (GPIB via NI-VISA) ---
KEITHLEY_VISA_ADDRESS = os.environ.get("KEITHLEY_VISA_ADDRESS", "GPIB0::22::INSTR")
KEITHLEY_TIMEOUT_MS = int(os.environ.get("KEITHLEY_TIMEOUT_MS", "5000"))

# --- Safety limits (temperature in °C) ---
SAFETY_MIN_TEMP = float(os.environ.get("SAFETY_MIN_TEMP", "0.0"))
SAFETY_MAX_TEMP = float(os.environ.get("SAFETY_MAX_TEMP", "100.0"))

# --- Data export ---
EXPORT_DELIMITER = "\t"
EXPORT_ENCODING = "utf-8"
EXPORT_DIR = "data"                      # folder next to main.py
EXPORT_FILENAME = "measurement"          # name without extension; program adds .txt itself
EXPORT_AUTO_CREATE_DIR = True            # create data folder if it does not exist

# --- Interface ---
UPDATE_INTERVAL_MS = 1000          # GUI update interval (ms)
PLOT_MAX_POINTS = 5000            # max points on plot (old ones removed)

# --- Temperature status threshold (°C) ---
# |actual - target| below this value => stable (0)
# actual > target + threshold => cooling (-1)
# actual < target - threshold => heating (1)
TEMP_STATUS_THRESHOLD = 0.5
