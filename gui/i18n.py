"""
Internationalization (i18n) for the TCM-X107 + Keithley 6487 GUI.
Supported languages: English (en) and Russian (ru).

Every user-facing string must be added here in BOTH languages.
Use gui.i18n.t("key", **kwargs) (imported as 'tr' in GUI modules) instead of
hardcoding text in the interface.
"""

from __future__ import annotations

LANGUAGES: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
}

DEFAULT_LANGUAGE = "en"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # --- Application ------------------------------------------------
        "app.title": "TCM-X107 + Keithley 6487 – Current vs Temperature Measurement",
        "status.ready": "Ready",
        "lang.label": "Language:",
        "theme.label": "Theme:",
        "theme.system": "System",
        "theme.light": "Light",
        "theme.dark": "Dark",

        # --- TCM-X107 ---------------------------------------------------
        "tcm.group": "TCM-X107 (Temperature Controller)",
        "btn.connect_tcm": "Connect TCM",
        "btn.disconnect_tcm": "Disconnect TCM",
        "lbl.status.disconnected": "Disconnected",
        "lbl.status.connected": "Connected",
        "label.port": "Port:",
        "label.target_temp": "Target T (°C):",
        "btn.set_temp": "Set T",
        "label.ramp_speed": "Ramp speed (°C/s):",
        "btn.set_ramp": "Set speed",
        "label.hold_time": "Hold time (s):",
        "btn.start_profile": "Start profile",
        "btn.stop_profile": "Stop profile",
        "chk.sw": "Output SW (heater)",
        "chk.oe": "Amplifier OE",
        "btn.emergency": "EMERGENCY STOP",
        "lbl.actual_temp": "Actual T",
        "lbl.setpoint_temp": "Setpoint T",
        "lbl.set_temp": "Set T",
        "lbl.actual_voltage": "Actual U",
        "lbl.actual_current": "Actual I",
        "lbl.info": "Status",
        "lbl.info.no_fault": "No error",
        "lbl.info.fault": "Error",

        # --- Keithley 6487 ----------------------------------------------
        "keithley.group": "Keithley 6487 (Picoammeter)",
        "label.gpib": "GPIB address:",
        "btn.connect_keithley": "Connect Keithley",
        "btn.disconnect_keithley": "Disconnect Keithley",
        "label.meas_count": "Measurements:",
        "btn.measure_once": "Measure once",
        "lbl.current": "Current",
        "lbl.error": "Error",
        "lbl.source_voltage": "Source voltage (V):",
        "btn.apply_voltage": "Apply voltage",
        "btn.start_meas": "Start measurements",
        "btn.stop_meas": "Stop measurements",
        "label.export_name": "File name:",
        "placeholder.export_name": "measurement",
        "chk.source_output": "Source output ON",
        "chk.source_ramp": "Smooth ramp",
        "lbl.ramp_algorithm": "Ramp algorithm:",
        "ramp.alg.linear": "Linear",
        "ramp.alg.nonlinear": "Nonlinear (decr. step)",
        "lbl.ramp_time": "Ramp time (s):",
        "lbl.ramp_steps": "Steps:",
        "btn.ramp_start": "Start ramping",
        "btn.ramp_pause": "Pause",
        "btn.ramp_resume": "Resume",
        "btn.ramp_stop": "Stop",
        "dlg.ramp_title": "Smooth voltage ramp",
        "lbl.ramp_progress": "Progress:",
        "lbl.ramp_voltage": "Voltage:",
        "lbl.ramp_current": "Current:",
        "err.keithley_not_disabled": "Keithley source output could not be disabled!",

        # --- Plots ------------------------------------------------------
        "plot.temp_time.title": "Temperature vs Time",
        "plot.current_time.title": "Current vs Time",
        "plot.current_temp.title": "Current vs Temperature",
        "axis.temperature": "Temperature",
        "axis.time": "Time",
        "axis.current": "Current",
        "axis.time_unit": "s",
        "axis.residuals": "I − ⟨I⟩",
        "axis.sample": "Sample",
        "tab.T_vs_t": "T vs t",
        "tab.i_vs_t": "I vs t",
        "tab.i_vs_T": "I vs T",
        "tab.all": "All",
        "plot.temp_all.title": "Temperature vs Time",
        "plot.current_time_all.title": "Current vs Time",
        "plot.current_temp_all.title": "Current vs Temperature",
        "plot.samples.title": "Current samples",

        # --- Messages / dialogs -----------------------------------------
        "msg.tcm_connected": "TCM-X107 connected: {port}",
        "msg.keithley_connected": "Keithley connected: {address}",
        "msg.keithley_disconnected": "Keithley disconnected",
        "msg.keithley_buffer": "Keithley buffer: {count} points",
        "msg.profile": "Profile: T={target}°C, hold {hold}s",
        "msg.profile_stopped": "Profile stopped",
        "msg.meas_started": "Measurements started",
        "msg.meas_stopped": "Measurements stopped",
        "msg.emergency_stop": "EMERGENCY STOP",
        "msg.source_voltage_set": "Source voltage set: {voltage} V",
        "dlg.emergency_title": "Emergency stop",
        "dlg.emergency_text": "Temperature controller output disabled!",
        "dlg.keithley": "Keithley",
        "err.title_tcm": "TCM connection error",
        "err.title_keithley": "Keithley connection error",
        "err.title_measure": "Measurement error",
        "err.title_safety": "Safety",
        "err.temp_range": "Temperature out of range [{min}, {max}]",
        "err.generic": "Error",
        "err.not_connected_keithley": "Instrument not connected",
        "err.connect_tcm_first": "Connect TCM-X107 first",
        "status.settings_saved": "Settings saved",
        
        # --- Export file conflict dialog ----------------------------------
        "dlg.file_exists": "File already exists",
        "dlg.file_exists_msg": 'The file "{name}" already exists. Choose an option:',
        "btn.continue": "Continue",
        "btn.overwrite": "Overwrite",
        "btn.keep_both": "Add suffix (_1, _2...)",
        "btn.cancel": "Cancel",
    },
    "ru": {
        # --- Application ------------------------------------------------
        "app.title": "TCM-X107 + Keithley 6487 – Измерение тока от температуры",
        "status.ready": "Готов",
        "lang.label": "Язык:",
        "theme.label": "Тема:",
        "theme.system": "Системная",
        "theme.light": "Светлая",
        "theme.dark": "Тёмная",

        # --- TCM-X107 ---------------------------------------------------
        "tcm.group": "TCM-X107 (Терморегулятор)",
        "btn.connect_tcm": "Подключить TCM",
        "btn.disconnect_tcm": "Отключить TCM",
        "lbl.status.disconnected": "Отключён",
        "lbl.status.connected": "Подключён",
        "label.port": "Порт:",
        "label.target_temp": "Целевая T (°C):",
        "btn.set_temp": "Задать T",
        "label.ramp_speed": "Скорость T (°C/с):",
        "btn.set_ramp": "Задать скорость",
        "label.hold_time": "Удержание (сек):",
        "btn.start_profile": "Пуск профиля",
        "btn.stop_profile": "Стоп профиля",
        "chk.sw": "Выход SW (нагреватель)",
        "chk.oe": "Усилитель OE",
        "btn.emergency": "АВАРИЙНЫЙ СТОП",
        "lbl.actual_temp": "Тек. T",
        "lbl.setpoint_temp": "Установка T",
        "lbl.set_temp": "Уст. T",
        "lbl.actual_voltage": "Тек. U",
        "lbl.actual_current": "Тек. I",
        "lbl.info": "Статус",
        "lbl.info.no_fault": "Ошибок нет",
        "lbl.info.fault": "Ошибка",

        # --- Keithley 6487 ----------------------------------------------
        "keithley.group": "Keithley 6487 (Пикоамперметр)",
        "label.gpib": "GPIB адрес:",
        "btn.connect_keithley": "Подключить Keithley",
        "btn.disconnect_keithley": "Отключить Keithley",
        "label.meas_count": "Измерений:",
        "btn.measure_once": "Измерить 1 раз",
        "lbl.current": "Ток",
        "lbl.error": "Погрешность",
        "lbl.source_voltage": "Напряжение источника (В):",
        "btn.apply_voltage": "Применить",
        "btn.start_meas": "Старт измерений",
        "btn.stop_meas": "Стоп измерений",
        "label.export_name": "Имя файла:",
        "placeholder.export_name": "measurement",
        "chk.source_output": "Источник включен",
        "chk.source_ramp": "Плавное нарастание",
        "lbl.ramp_algorithm": "Алгоритм нарастания:",
        "ramp.alg.linear": "Линейное",
        "ramp.alg.nonlinear": "Нелинейное (убыв. шаг)",
        "lbl.ramp_time": "Время нарастания (с):",
        "lbl.ramp_steps": "Шагов:",
        "btn.ramp_start": "Начать нарастание",
        "btn.ramp_pause": "Пауза",
        "btn.ramp_resume": "Продолжить",
        "btn.ramp_stop": "Стоп",
        "dlg.ramp_title": "Плавное нарастание напряжения",
        "lbl.ramp_progress": "Прогресс:",
        "lbl.ramp_voltage": "Напряжение:",
        "lbl.ramp_current": "Ток:",
        "err.keithley_not_disabled": "Не удалось отключить выход источника Keithley!",

        # --- Plots ------------------------------------------------------
        "plot.temp_time.title": "Температура vs Время",
        "plot.current_time.title": "Ток vs Время",
        "plot.current_temp.title": "Ток vs Температура",
        "axis.temperature": "Температура",
        "axis.time": "Время",
        "axis.current": "Ток",
        "axis.time_unit": "с",
        "axis.residuals": "I − ⟨I⟩",
        "axis.sample": "Выборка",
        "tab.T_vs_t": "T vs t",
        "tab.i_vs_t": "I vs t",
        "tab.i_vs_T": "I vs T",
        "tab.all": "Все",
        "plot.temp_all.title": "Температура vs Время",
        "plot.current_time_all.title": "Ток vs Время",
        "plot.current_temp_all.title": "Ток vs Температура",
        "plot.samples.title": "Пробы тока",

        # --- Messages / dialogs -----------------------------------------
        "msg.tcm_connected": "TCM-X107 подключён: {port}",
        "msg.keithley_connected": "Keithley подключён: {address}",
        "msg.keithley_disconnected": "Keithley отключён",
        "msg.keithley_buffer": "Буфер Keithley: {count} точек",
        "msg.profile": "Профиль: T={target}°C, удержание {hold}с",
        "msg.profile_stopped": "Профиль остановлен",
        "msg.meas_started": "Измерения запущены",
        "msg.meas_stopped": "Измерения остановлены",
        "msg.emergency_stop": "АВАРИЙНЫЙ СТОП",
        "msg.source_voltage_set": "Напряжение источника установлено: {voltage} В",
        "dlg.emergency_title": "Аварийный стоп",
        "dlg.emergency_text": "Выход терморегулятора отключён!",
        "dlg.keithley": "Keithley",
        "err.title_tcm": "Ошибка подключения TCM",
        "err.title_keithley": "Ошибка подключения Keithley",
        "err.title_measure": "Ошибка измерения",
        "err.title_safety": "Безопасность",
        "err.temp_range": "Температура вне диапазона [{min}, {max}]",
        "err.generic": "Ошибка",
        "err.not_connected_keithley": "Прибор не подключён",
        "err.connect_tcm_first": "Сначала подключите TCM-X107",
        
        # --- Export file conflict dialog ----------------------------------
        "dlg.file_exists": "Файл уже существует",
        "dlg.file_exists_msg": 'Файл "{name}" уже существует. Выберите действие:',
        "btn.continue": "Продолжить запись",
        "btn.overwrite": "Перезаписать",
        "btn.keep_both": "Добавить суффикс (_1, _2...)",
        "btn.cancel": "Отмена",
        "status.settings_saved": "Настройки сохранены",
    },
}

_current_language = DEFAULT_LANGUAGE


def get_available_languages() -> list[tuple[str, str]]:
    """Return a list (code, name) of supported languages."""
    return list(LANGUAGES.items())


def set_language(code: str) -> None:
    """Set the current interface language."""
    global _current_language
    if code not in LANGUAGES:
        raise ValueError(f"Unknown language: {code!r}")
    _current_language = code


def get_language() -> str:
    """Return the code of the current language."""
    return _current_language


def tr(key: str, **kwargs) -> str:
    """Translate the key into the current language.
    If the template contains fields like {name}, pass them as kwargs.
    """
    try:
        template = TRANSLATIONS[_current_language][key]
    except KeyError:
        template = TRANSLATIONS["en"].get(key, key)
    return template.format(**kwargs) if kwargs else template
