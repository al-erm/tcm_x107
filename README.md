# TCM-X107 + Keithley 6487
![License](https://img.shields.io/badge/License-MIT-3B8133)
![python](https://img.shields.io/badge/python-3.10%2B-FFE873?logo=python)
![PyQt6-Qt6](https://img.shields.io/badge/PyQt6--Qt6-6.6.0%2B-2CDE85)

Application for measuring current vs temperature: TCM-X107 temperature controller (USB → RS-232) and Keithley 6487 picoammeter (GPIB via NI-VISA) with real-time graphs and tab-separated data export.

<img width="1314" height="891" alt="image" src="https://github.com/user-attachments/assets/f97305aa-6287-4ddd-8e2e-730c4f0407b9" />

---

## Features

- Connecting and controlling TCM-X107 via virtual COM port
- Keithley 6487 current measurement with buffered acquisition
- Temperature profile: target temperature, ramp speed, hold time
- Real-time graphs: T-t, I-t, I-T (tabs)
- Current samples graph (residuals from mean)
- Export measurements to `.txt` (tab-delimited, write per point to disk)
- Emergency stop (disables TCM-X107 output and stops profile)

> Interface in two languages (en/ru) with runtime switching and three themes (system/light/dark) configurable directly in the program window.

---

## Tech stack

| Component | Library |
|-----------|---------|
| Language | Python 3.10+ |
| GUI | PyQt6 |
| Graphs | pyqtgraph |
| COM port (TCM-X107) | pyserial |
| GPIB/VISA (Keithley 6487) | pyvisa + NI-VISA |
| Math | numpy, scipy |

---

## Requirements

### Mandatory hardware
- TCM-X107, connected via USB -> RS-232 (virtual COM port)

### Optional hardware
- Keithley 6487, connected via GPIB (National Instruments PCIe-GPIB or similar)
- Installed **NI-VISA** on the computer (for Keithley)

**Keithley 6487 is optional.** If not connected – the program starts and works, just zeros instead of current in the file and empty graphs.
Can smooth increase the voltage.

<img width="422" height="252" alt="image" src="https://github.com/user-attachments/assets/946111fb-5509-42f7-b540-d56ab5a420c3" />


### Software

```bash
pip install -r requirements.txt
```

`requirements.txt` contains: `pyqt6`, `pyqtgraph`, `pyserial`, `pyvisa`, `numpy`, `scipy`.

---

## Quick start

```bash
cd <path_to_project>
pip install -r requirements.txt
python main.py
```

### Ports and addresses setup

All settings are taken from `config.py` or environment variables:

```ini
TCM_PORT=COM10                          # COM port TCM-X107
TCM_BAUDRATE=57600                      # Baud rate
KEITHLEY_VISA_ADDRESS=GPIB0::22::INSTR  # GPIB address Keithley 6487
SAFETY_MIN_TEMP=0.0                     # Lower temperature limit (°C)
SAFETY_MAX_TEMP=100.0                   # Upper temperature limit (°C)
TEMP_STATUS_THRESHOLD=0.5               # Temperature stability threshold (°C)
```

Ports and addresses can also be changed directly in the program window – they are saved to `core/settings.json`.

---

## Project structure

```
tcm_x107/
├── main.py                 # Entry point (QApplication)
├── config.py               # Ports, addresses, limits, export settings
├── requirements.txt        # Dependencies
│
├── instruments/            # Hardware drivers
│   ├── tcm_x107.py         # TCM-X107 (serial port)
│   └── keithley_6487.py    # Keithley 6487 (GPIB/VISA)
│
├── core/                   # Business logic
│   ├── controller.py       # Temperature profile (target, hold, emergency stop)
│   ├── data_manager.py     # Export points to .txt (write immediately to disk)
│   ├── math_processing.py  # Median filtering, outliers, confidence interval
│   ├── settings_manager.py # Settings saving (settings.json)
│   └── settings.json       # User settings (created on first save)
│
├── gui/                    # Interface (single window)
│   ├── main_window.py      # Main window: controls on the left, graphs on the right
│   ├── i18n.py             # Internationalization (en / ru)
│   └── themes.py           # Themes: system / light / dark
│
└── data/                   # Measurements folder (created automatically)
```

---

## Export file format

Measurements are written to `data/<name>.txt`, tab delimiter:

```
YYYY-MM-DD HH:mm:ss          <- creation date/time
Keithley voltage:            <- informational line

time (min)   temperature (C)   current (A)   current_error (A)   status
0.028        27.0175	       6.058988e-05  9.885752e-09        1
0.048        27.0365	       6.059321e-05  1.081883e-08        1
0.067        27.0551	       6.058543e-05  6.944516e-09        1
...
```

Status codes:
- `-1` – cooling (temperature above target by more than the threshold)
- `0` – stable (within threshold, set in `config.TEMP_STATUS_THRESHOLD`, default ±0.5 °C)
- `1` – heating (temperature below target by more than the threshold)

### Filename

If the default name (`measurement`) is left, the current date is added automatically: `measurement_YYYY_MM_DD.txt`.

If you specify your own name and a file with that name already exists, the program offers: continue recording, add suffix (`_1`, `_2`…), overwrite, or cancel.

---

## Safety

- Temperature range limited by `SAFETY_MIN_TEMP` / `SAFETY_MAX_TEMP` (default 0–100 °C).
- **EMERGENCY STOP** button disables TCM-X107 output (`TCSW=0`, `TCOE=0`) and stops the profile.
- On program close: profile stops, both instruments disconnect.

---

## Running on Linux

If `GLib-GIO-ERROR` about `org.gnome.settings-daemon.plugins.xsettings` appears – it is a system/GNOME issue, not a code bug. Fix: run under X11 instead of Wayland.

---

## License

This project was developed for internal measurement tasks. Distributed without warranties.

This project is distributed under the **MIT** license. See [LICENSE](LICENSE) for details.
