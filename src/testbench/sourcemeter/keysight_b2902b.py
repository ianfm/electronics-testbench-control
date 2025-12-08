from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Sequence

from testbench.core.scpi import SCPIDriver, SCPISettings
from testbench.domains.sourcemeter import ScpiSourceMeter


Scalar = float | int | bool | str


@dataclass
class MeasurementRecord:
    """Container for parsed measurement elements returned by READ?/FETCh?."""

    values: dict[str, float]

    def get(self, element: str) -> float | None:
        return self.values.get(element.upper())

    @property
    def voltage(self) -> float | None:
        return self.get("VOLT")

    @property
    def current(self) -> float | None:
        return self.get("CURR")

    @property
    def resistance(self) -> float | None:
        return self.get("RES")

    @property
    def timestamp(self) -> float | None:
        return self.get("TIME")


class KeysightB2902B(ScpiSourceMeter):
    """
    Keysight B2902B sourcemeter.

    The instrument firmware exposes large SCPI subsystems for different domains
    (source configuration, measurement setup, triggering, trace buffers, etc.).
    To keep the driver maintainable we model those as dedicated helper objects:

    * ``system``  -> :SYSTem commands (errors, status, etc.)
    * ``source``  -> :SOURce commands (per-channel source setup)
    * ``sense``   -> :SENSe commands (measurement side)
    * ``output``  -> :OUTPut commands (channel enable, protection, etc.)
    * ``trigger`` -> :TRIGger commands
    * ``lxi``     -> :LXI specific trigger/event helpers
    * ``fetch``   -> :FETCh data extraction
    * ``format``  -> :FORMat helpers for binary/ASCII payloads
    * ``read``    -> :READ subsystem (one-shot measurement)
    * ``measure`` -> :MEASure subsystem (spot measurement)
    * ``calculate`` -> :CALCulate subsystem
    * ``trace``   -> :TRACe buffer helpers
    * ``hardcopy``-> :HCOPy screen dumps
    * ``display`` -> :DISPlay configuration
    * ``memory``  -> :MMEMory file management
    * ``program`` -> :PROGram memory handling
    * ``status``  -> :STATus bits.

    Each subsystem gets direct access to the shared SCPIDriver so commands stay
    close to the manual organization. Instrument-level helpers delegate into
    those subsystems for ergonomics while keeping backward compatibility.
    """

    ID_SUBSTRINGS = ("B2902",)

    def __init__(
        self,
        settings: SCPISettings = SCPISettings(),
        resource_name: str | None = None,
    ) -> None:
        super().__init__(settings, resource_name=resource_name)
        self.format_state = _FormatState(self.driver)
        self.system = _SystemSubsystem(self.driver)
        self.source = _SourceSubsystem(self.driver)
        self.sense = _SenseSubsystem(self.driver)
        self.output = _OutputSubsystem(self.driver)
        self.trigger = _TriggerSubsystem(self.driver)
        self.lxi = _LXISubsystem(self.driver)
        self.fetch = _FetchSubsystem(self.driver)
        self.format = _FormatSubsystem(self.driver)
        self.read = _ReadSubsystem(self.driver)
        self.measure = _MeasureSubsystem(self.driver)
        self.calculate = _CalculateSubsystem(self.driver)
        self.trace = _TraceSubsystem(self.driver)
        self.hardcopy = _HardcopySubsystem(self.driver)
        self.display = _DisplaySubsystem(self.driver)
        self.memory = _MemorySubsystem(self.driver)
        self.program = _ProgramSubsystem(self.driver)
        self.status = _StatusSubsystem(self.driver)
        if self.online():
            self.driver.write("*RST")
            time.sleep(self.driver.settings.query_delay)

    def read_error_codes(self) -> list[int]:
        """Compatibility wrapper for ``self.system.read_error_codes()``."""
        return self.system.read_error_codes()

    def clear_error_queue(self) -> None:
        """Compatibility wrapper for ``self.system.clear_error_queue()``."""
        self.system.clear_error_queue()

    def set_current_source_mode(self, mode: str, channel: int | None = None) -> None:
        self.source.set_current_mode(mode, channel)

    def get_current_source_mode(self, channel: int | None = None) -> str:
        return self.source.get_current_mode(channel)

    def set_voltage_source_mode(self, mode: str, channel: int | None = None) -> None:
        self.source.set_voltage_mode(mode, channel)

    def get_voltage_source_mode(self, channel: int | None = None) -> str:
        return self.source.get_voltage_mode(channel)

    def measure_voltage_current(self, channel: int = 1) -> tuple[float, float]:
        """
        Trigger a blocking measurement and return (voltage, current).

        Uses ``READ?`` with :FORMat configured to return VOLT,CURR pairs so the
        response matches the selected measurement functions.
        """
        if not self.online():
            raise RuntimeError("Instrument not connected")
        self.sense.enable_function("VOLT", channel=channel)
        self.sense.enable_function("CURR", channel=channel)
        voltage = float(self.driver.query(f"MEAS:VOLT? (@{channel})"))
        current = float(self.driver.query(f"MEAS:CURR? (@{channel})"))
        return voltage, current

    def read_measurements(
        self, channels: Iterable[int], required_elements: Sequence[str] | None = None
    ) -> list[MeasurementRecord]:
        """
        Issue ``READ?`` for the specified channels and parse into records.

        The format state is updated so the response matches ``required_elements``
        if provided; otherwise the existing :FORM:ELEM:SENS configuration is used.
        """
        if not self.online():
            raise RuntimeError("Instrument not connected")
        channel_list = list(channels)
        if not channel_list:
            raise ValueError("At least one channel is required")
        channel_clause = SCPIDriver.format_channel_list(channel_list)
        self.format_state.ensure_ascii()
        if required_elements:
            self.format_state.ensure_elements(required_elements)
        response = self.driver.query(f"READ? {channel_clause}")
        records = self.format_state.parse_measurements(response)
        if len(records) != len(channel_list):
            raise RuntimeError(
                f"Expected {len(channel_list)} records, received {len(records)}"
            )
        return records


class _B2902BSubsystem:
    """Shared plumbing for subsystem helpers."""

    def __init__(self, driver: SCPIDriver) -> None:
        self._driver = driver

    @property
    def online(self) -> bool:
        return self._driver.resource is not None

    def _format_value(self, value: Scalar) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    def _format_csv(self, values: Sequence[Scalar]) -> str:
        return ",".join(self._format_value(v) for v in values)

    def _parse_bool(self, response: str) -> bool:
        normalized = response.strip().upper()
        return normalized in {"1", "ON", "TRUE"}

    def _parse_float_list(self, response: str) -> list[float]:
        if not response.strip():
            return []
        return [float(part) for part in response.split(",")]

    def _parse_int(self, response: str) -> int:
        return int(float(response))

    def _parse_float(self, response: str) -> float:
        return float(response)


class _NamespacedSubsystem(_B2902BSubsystem):
    """Helper that captures the root SCPI header (e.g. ``SYST`` or ``TRIG``)."""

    def __init__(self, driver: SCPIDriver, header: str) -> None:
        super().__init__(driver)
        self._header = header

    def _command(self, suffix: str) -> str:
        """Build a SCPI command path anchored at this subsystem."""
        return f":{self._header}:{suffix}"


class _ChannelSubsystem(_NamespacedSubsystem):
    """Subsystems that accept an optional channel suffix (``SOUR1``/``SOUR2``)."""

    def __init__(self, driver: SCPIDriver, header: str) -> None:
        super().__init__(driver, header)

    def _prefix(self, channel: int | None) -> str:
        if channel is None:
            return self._header
        if channel not in (1, 2):
            raise ValueError("channel must be 1 or 2 for the B2902B")
        return f"{self._header}{channel}"

    def _write_channel_value(self, channel: int | None, suffix: str, value: Scalar) -> None:
        command = f"{self._prefix(channel)}:{suffix}"
        self._driver.write(f"{command} {self._format_value(value)}")

    def _query_channel_value(
        self, channel: int | None, suffix: str, option: str | None = None
    ) -> str:
        command = f"{self._prefix(channel)}:{suffix}?"
        if option:
            command = f"{command} {option}"
        return self._driver.query(command)

    def _query_channel_float(
        self, channel: int | None, suffix: str, option: str | None = None
    ) -> float:
        return self._parse_float(self._query_channel_value(channel, suffix, option))

    def _query_channel_int(
        self, channel: int | None, suffix: str, option: str | None = None
    ) -> int:
        return self._parse_int(self._query_channel_value(channel, suffix, option))

    def _query_channel_bool(
        self, channel: int | None, suffix: str, option: str | None = None
    ) -> bool:
        return self._parse_bool(self._query_channel_value(channel, suffix, option))


class _SystemSubsystem(_NamespacedSubsystem):
    """System-level helpers: error queue, status registers, etc."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "SYST")

    def read_error_codes(self) -> list[int]:
        """
        Read and reset the instrument error queue.

        ``:SYST:ERR:CODE:ALL?`` returns a comma-separated list of codes and
        clears the queue.
        """
        if not self.online:
            return []
        response = self._driver.query(self._command("ERR:CODE:ALL?"))
        stripped = response.strip()
        if not stripped:
            return []
        try:
            return [int(code.strip()) for code in stripped.split(",") if code.strip()]
        except ValueError as exc:  # pragma: no cover - hardware-specific behavior
            raise RuntimeError(
                f"Unexpected B2902B error response: {response!r}"
            ) from exc

    def clear_error_queue(self) -> None:
        """Clear the error/event queue without returning the codes."""
        if not self.online:
            return
        self._driver.query(self._command("ERR:CODE:ALL?"))


class _SourceSubsystem(_ChannelSubsystem):
    """Source configuration (voltage/current modes, list/sweep, ranges, etc.)."""

    _VALID_SOURCE_MODES = {
        "FIX": "FIX",
        "FIXED": "FIX",
        "LIST": "LIST",
        "SWE": "SWE",
        "SWEEP": "SWE",
    }

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "SOUR")

    # Sweep center/span -----------------------------------------------------
    def set_current_center(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("CURR", "CENT", value, channel)

    def get_current_center(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("CURR", "CENT", channel, preset)

    def set_voltage_center(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("VOLT", "CENT", value, channel)

    def get_voltage_center(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("VOLT", "CENT", channel, preset)

    def set_current_span(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("CURR", "SPAN", value, channel)

    def get_current_span(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("CURR", "SPAN", channel, preset)

    def set_voltage_span(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("VOLT", "SPAN", value, channel)

    def get_voltage_span(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("VOLT", "SPAN", channel, preset)

    # Output levels ---------------------------------------------------------
    def set_current_level(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:LEV:IMM:AMPL", level)

    def get_current_level(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "CURR:LEV:IMM:AMPL", preset)

    def set_voltage_level(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:LEV:IMM:AMPL", level)

    def get_voltage_level(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:LEV:IMM:AMPL", preset)

    def set_current_trigger_level(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:LEV:TRIG:AMPL", level)

    def get_current_trigger_level(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "CURR:LEV:TRIG:AMPL", preset)

    def set_voltage_trigger_level(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:LEV:TRIG:AMPL", level)

    def get_voltage_trigger_level(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "VOLT:LEV:TRIG:AMPL", preset)

    # Mode (FIX/LIST/SWEEP) -------------------------------------------------
    def set_current_mode(self, mode: str, channel: int | None = None) -> None:
        self._set_mode("CURR", mode, channel)

    def get_current_mode(self, channel: int | None = None) -> str:
        return self._get_mode("CURR", channel)

    def set_voltage_mode(self, mode: str, channel: int | None = None) -> None:
        self._set_mode("VOLT", mode, channel)

    def get_voltage_mode(self, channel: int | None = None) -> str:
        return self._get_mode("VOLT", channel)

    # Sweep points ----------------------------------------------------------
    def set_current_points(self, points: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:POIN", points)

    def get_current_points(self, channel: int | None = None, preset: str | None = None) -> int:
        return self._query_channel_int(channel, "CURR:POIN", preset)

    def set_voltage_points(self, points: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:POIN", points)

    def get_voltage_points(self, channel: int | None = None, preset: str | None = None) -> int:
        return self._query_channel_int(channel, "VOLT:POIN", preset)

    # Source ranges ---------------------------------------------------------
    def set_current_range(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:RANG", value)

    def get_current_range(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "CURR:RANG")

    def set_voltage_range(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:RANG", value)

    def get_voltage_range(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:RANG")

    def set_current_auto_range_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:RANG:AUTO", enabled)

    def is_current_auto_range_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "CURR:RANG:AUTO")

    def set_voltage_auto_range_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:RANG:AUTO", enabled)

    def is_voltage_auto_range_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "VOLT:RANG:AUTO")

    def set_current_auto_range_lower_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "CURR:RANG:AUTO:LLIM", value)

    def get_current_auto_range_lower_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "CURR:RANG:AUTO:LLIM")

    def set_voltage_auto_range_lower_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "VOLT:RANG:AUTO:LLIM", value)

    def get_voltage_auto_range_lower_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:RANG:AUTO:LLIM")

    def set_current_range_priority(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:RANG:RPR", mode)

    def get_current_range_priority(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "CURR:RANG:RPR")

    def set_voltage_range_priority(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:RANG:RPR", mode)

    def get_voltage_range_priority(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "VOLT:RANG:RPR")

    # Sweep start/stop -------------------------------------------------------
    def set_current_start(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("CURR", "STAR", value, channel)

    def get_current_start(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("CURR", "STAR", channel, preset)

    def set_voltage_start(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("VOLT", "STAR", value, channel)

    def get_voltage_start(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("VOLT", "STAR", channel, preset)

    def set_current_stop(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("CURR", "STOP", value, channel)

    def get_current_stop(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("CURR", "STOP", channel, preset)

    def set_voltage_stop(self, value: Scalar, channel: int | None = None) -> None:
        self._set_sweep_value("VOLT", "STOP", value, channel)

    def get_voltage_stop(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._get_sweep_value("VOLT", "STOP", channel, preset)

    # Transient behavior -----------------------------------------------------
    def set_current_transient_speed(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:TRAN:SPE", mode)

    def get_current_transient_speed(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "CURR:TRAN:SPE")

    def set_voltage_transient_speed(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:TRAN:SPE", mode)

    def get_voltage_transient_speed(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "VOLT:TRAN:SPE")

    def set_current_step(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:STEP", value)

    def get_current_step(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "CURR:STEP", preset)

    def set_voltage_step(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:STEP", value)

    def get_voltage_step(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:STEP", preset)

    # Digital I/O ------------------------------------------------------------
    def set_digital_data(self, value: int) -> None:
        self._driver.write(f"{self._header}:DIG:DATA {value}")

    def read_digital_data(self) -> int:
        return self._parse_int(self._driver.query(f"{self._header}:DIG:DATA?"))

    def set_digital_pin_function(self, pin: int, function: str) -> None:
        pin_label = self._validate_ext_pin(pin)
        self._driver.write(f"{self._header}:DIG:EXT{pin_label}:FUNC {function}")

    def get_digital_pin_function(self, pin: int) -> str:
        pin_label = self._validate_ext_pin(pin)
        return self._driver.query(f"{self._header}:DIG:EXT{pin_label}:FUNC?")

    def set_digital_pin_polarity(self, pin: int, polarity: str) -> None:
        pin_label = self._validate_ext_pin(pin)
        self._driver.write(f"{self._header}:DIG:EXT{pin_label}:POL {polarity}")

    def get_digital_pin_polarity(self, pin: int) -> str:
        pin_label = self._validate_ext_pin(pin)
        return self._driver.query(f"{self._header}:DIG:EXT{pin_label}:POL?")

    def set_digital_pin_trigger_output_position(self, pin: int, position: str) -> None:
        pin_label = self._validate_ext_pin(pin)
        self._driver.write(
            f"{self._header}:DIG:EXT{pin_label}:TOUT:EDGE:POS {position}"
        )

    def get_digital_pin_trigger_output_position(self, pin: int) -> str:
        pin_label = self._validate_ext_pin(pin)
        return self._driver.query(f"{self._header}:DIG:EXT{pin_label}:TOUT:EDGE:POS?")

    def set_digital_pin_trigger_output_width(self, pin: int, width: Scalar) -> None:
        pin_label = self._validate_ext_pin(pin)
        self._driver.write(
            f"{self._header}:DIG:EXT{pin_label}:TOUT:EDGE:WIDT {self._format_value(width)}"
        )

    def get_digital_pin_trigger_output_width(self, pin: int, preset: str | None = None) -> float:
        pin_label = self._validate_ext_pin(pin)
        command = f"{self._header}:DIG:EXT{pin_label}:TOUT:EDGE:WIDT?"
        if preset:
            command = f"{command} {preset}"
        return self._parse_float(self._driver.query(command))

    def set_digital_pin_trigger_output_type(self, pin: int, trigger_type: str) -> None:
        pin_label = self._validate_ext_pin(pin)
        self._driver.write(
            f"{self._header}:DIG:EXT{pin_label}:TOUT:TYPE {trigger_type}"
        )

    def get_digital_pin_trigger_output_type(self, pin: int) -> str:
        pin_label = self._validate_ext_pin(pin)
        return self._driver.query(f"{self._header}:DIG:EXT{pin_label}:TOUT:TYPE?")

    def set_internal_trigger_output_position(self, line: int, position: str) -> None:
        line_label = self._validate_internal_line(line)
        self._driver.write(
            f"{self._header}:DIG:INT{line_label}:TOUT:EDGE:POS {position}"
        )

    def get_internal_trigger_output_position(self, line: int) -> str:
        line_label = self._validate_internal_line(line)
        return self._driver.query(f"{self._header}:DIG:INT{line_label}:TOUT:EDGE:POS?")

    # Source function --------------------------------------------------------
    def set_function_mode(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "FUNC:MODE", mode)

    def get_function_mode(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "FUNC:MODE")

    def set_function_shape(self, shape: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "FUNC:SHAP", shape)

    def get_function_shape(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "FUNC:SHAP")

    def set_continuous_trigger_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "FUNC:TRIG:CONT", enabled)

    def is_continuous_trigger_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "FUNC:TRIG:CONT")

    # List sweeps ------------------------------------------------------------
    def set_current_list(self, values: Sequence[Scalar], channel: int | None = None) -> None:
        self._set_list_data("CURR", values, channel)

    def get_current_list(self, channel: int | None = None) -> list[float]:
        return self._get_list_data("CURR", channel)

    def append_current_list(self, values: Sequence[Scalar], channel: int | None = None) -> None:
        self._append_list_data("CURR", values, channel)

    def set_voltage_list(self, values: Sequence[Scalar], channel: int | None = None) -> None:
        self._set_list_data("VOLT", values, channel)

    def get_voltage_list(self, channel: int | None = None) -> list[float]:
        return self._get_list_data("VOLT", channel)

    def append_voltage_list(self, values: Sequence[Scalar], channel: int | None = None) -> None:
        self._append_list_data("VOLT", values, channel)

    def get_current_list_points(self, channel: int | None = None) -> int:
        return self._query_channel_int(channel, "LIST:CURR:POIN")

    def get_voltage_list_points(self, channel: int | None = None) -> int:
        return self._query_channel_int(channel, "LIST:VOLT:POIN")

    def set_current_list_start_index(self, index: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "LIST:CURR:STAR", index)

    def get_current_list_start_index(self, channel: int | None = None) -> int:
        return self._query_channel_int(channel, "LIST:CURR:STAR")

    def set_voltage_list_start_index(self, index: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "LIST:VOLT:STAR", index)

    def get_voltage_list_start_index(self, channel: int | None = None) -> int:
        return self._query_channel_int(channel, "LIST:VOLT:STAR")

    # Pulse settings ---------------------------------------------------------
    def set_pulse_delay(self, delay: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "PULS:DEL", delay)

    def get_pulse_delay(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "PULS:DEL", preset)

    def set_pulse_width(self, width: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "PULS:WIDT", width)

    def get_pulse_width(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "PULS:WIDT", preset)

    # Sweep options ----------------------------------------------------------
    def set_sweep_direction(self, direction: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SWE:DIR", direction)

    def get_sweep_direction(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "SWE:DIR")

    def set_sweep_points(self, points: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SWE:POIN", points)

    def get_sweep_points(self, channel: int | None = None, preset: str | None = None) -> int:
        return self._query_channel_int(channel, "SWE:POIN", preset)

    def set_sweep_range_mode(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SWE:RANG", mode)

    def get_sweep_range_mode(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "SWE:RANG")

    def set_sweep_spacing(self, spacing: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SWE:SPAC", spacing)

    def get_sweep_spacing(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "SWE:SPAC")

    def set_sweep_mode(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SWE:STA", mode)

    def get_sweep_mode(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "SWE:STA")

    # Trigger output ---------------------------------------------------------
    def set_trigger_output_signals(
        self, outputs: Sequence[str], channel: int | None = None
    ) -> None:
        if not outputs:
            raise ValueError("At least one trigger output destination is required")
        value = ",".join(outputs)
        self._write_channel_value(channel, "TOUT:SIGN", value)

    def get_trigger_output_signals(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "TOUT:SIGN")

    def set_trigger_output_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "TOUT:STAT", enabled)

    def is_trigger_output_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "TOUT:STAT")

    # Wait behavior ----------------------------------------------------------
    def set_wait_auto_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:AUTO", enabled)

    def is_wait_auto_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "WAIT:AUTO")

    def set_wait_gain(self, gain: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:GAIN", gain)

    def get_wait_gain(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "WAIT:GAIN", preset)

    def set_wait_offset(self, offset: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:OFFS", offset)

    def get_wait_offset(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "WAIT:OFFS", preset)

    def set_wait_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:STAT", enabled)

    def is_wait_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "WAIT:STAT")

    # Internal helpers -------------------------------------------------------
    def _set_mode(self, function: str, mode: str, channel: int | None) -> None:
        scpi_mode = self._normalize_source_mode(mode)
        self._write_channel_value(channel, f"{function}:MODE", scpi_mode)

    def _get_mode(self, function: str, channel: int | None) -> str:
        return self._query_channel_value(channel, f"{function}:MODE")

    def _normalize_source_mode(self, mode: str) -> str:
        key = mode.strip().upper()
        scpi_mode = self._VALID_SOURCE_MODES.get(key)
        if scpi_mode is None:
            raise ValueError(
                f"Unsupported source mode {mode!r}. "
                "Valid modes: FIX, LIST, SWE (SWEEP)."
            )
        return scpi_mode

    def _set_sweep_value(
        self, function: str, parameter: str, value: Scalar, channel: int | None
    ) -> None:
        self._write_channel_value(channel, f"{function}:{parameter}", value)

    def _get_sweep_value(
        self, function: str, parameter: str, channel: int | None, preset: str | None
    ) -> float:
        return self._query_channel_float(channel, f"{function}:{parameter}", preset)

    def _set_list_data(
        self, function: str, values: Sequence[Scalar], channel: int | None
    ) -> None:
        formatted = self._format_csv(values)
        self._write_channel_value(channel, f"LIST:{function}", formatted)

    def _append_list_data(
        self, function: str, values: Sequence[Scalar], channel: int | None
    ) -> None:
        formatted = self._format_csv(values)
        self._write_channel_value(channel, f"LIST:{function}:APP", formatted)

    def _get_list_data(self, function: str, channel: int | None) -> list[float]:
        response = self._query_channel_value(channel, f"LIST:{function}")
        return self._parse_float_list(response)

    def _validate_ext_pin(self, pin: int) -> int:
        if pin < 1 or pin > 14:
            raise ValueError("GPIO pin must be between 1 and 14")
        return pin

    def _validate_internal_line(self, line: int) -> int:
        if line not in (1, 2):
            raise ValueError("Internal trigger line must be 1 or 2")
        return line


class _SenseSubsystem(_ChannelSubsystem):
    """Measurement configuration (:SENSe)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "SENS")

    # Measurement function selection ----------------------------------------
    def set_functions(self, functions: Sequence[str], channel: int | None = None) -> None:
        formatted = self._format_function_list(functions)
        self._write_channel_value(channel, "FUNC", formatted)

    def get_functions(self, channel: int | None = None) -> list[str]:
        response = self._query_channel_value(channel, "FUNC")
        return self._parse_function_list(response)

    def enable_function(self, function: str, channel: int | None = None) -> None:
        formatted = self._format_function_list([function])
        self._write_channel_value(channel, "FUNC:ON", formatted)

    def disable_function(self, function: str, channel: int | None = None) -> None:
        formatted = self._format_function_list([function])
        self._write_channel_value(channel, "FUNC:OFF", formatted)

    # Aperture / NPLC -------------------------------------------------------
    def set_current_aperture(self, time_seconds: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:APER", time_seconds)

    def get_current_aperture(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "CURR:APER", preset)

    def set_current_nplc(self, nplc: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:NPLC", nplc)

    def get_current_nplc(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "CURR:NPLC", preset)

    def set_current_aperture_auto(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:APER:AUTO", enabled)

    def is_current_aperture_auto(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "CURR:APER:AUTO")

    def set_current_nplc_auto(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:NPLC:AUTO", enabled)

    def is_current_nplc_auto(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "CURR:NPLC:AUTO")

    # Ranging ---------------------------------------------------------------
    def set_current_range(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:RANG", value)

    def get_current_range(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "CURR:RANG")

    def set_voltage_range(self, value: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:RANG", value)

    def get_voltage_range(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:RANG")

    def set_current_auto_range_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "CURR:RANG:AUTO", enabled)

    def is_current_auto_range_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "CURR:RANG:AUTO")

    def set_voltage_auto_range_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "VOLT:RANG:AUTO", enabled)

    def is_voltage_auto_range_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "VOLT:RANG:AUTO")

    def set_current_auto_range_lower_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "CURR:RANG:AUTO:LLIM", value)

    def get_current_auto_range_lower_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "CURR:RANG:AUTO:LLIM")

    def set_voltage_auto_range_lower_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "VOLT:RANG:AUTO:LLIM", value)

    def get_voltage_auto_range_lower_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:RANG:AUTO:LLIM")

    def set_current_auto_range_upper_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "CURR:RANG:AUTO:ULIM", value)

    def get_current_auto_range_upper_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "CURR:RANG:AUTO:ULIM")

    def set_voltage_auto_range_upper_limit(
        self, value: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "VOLT:RANG:AUTO:ULIM", value)

    def get_voltage_auto_range_upper_limit(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "VOLT:RANG:AUTO:ULIM")

    # Compliance / protection ----------------------------------------------
    def set_current_compliance(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "CURR:PROT:LEV", level)

    def get_current_compliance(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "CURR:PROT:LEV", preset)

    def set_voltage_compliance(self, level: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "VOLT:PROT:LEV", level)

    def get_voltage_compliance(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "VOLT:PROT:LEV", preset)

    def compliance_tripped(self, function: str, channel: int | None = None) -> bool:
        func = function.strip().upper()
        if func not in {"CURR", "VOLT"}:
            raise ValueError("function must be CURR or VOLT")
        return self._query_channel_bool(channel, f"{func}:PROT:TRIP")

    def enable_remote_sense(self, enabled: bool | str, channel: int | None = None) -> None:
        """
        Enable or disable remote sensing (4-wire) for the measurement subsystem.
        """
        self._write_channel_value(channel, "REM", enabled)

    def is_remote_sense_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "REM")

    # Wait behavior ---------------------------------------------------------
    def set_wait_auto_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:AUTO", enabled)

    def is_wait_auto_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "WAIT:AUTO")

    def set_wait_gain(self, gain: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:GAIN", gain)

    def get_wait_gain(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "WAIT:GAIN", preset)

    def set_wait_offset(self, offset: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:OFFS", offset)

    def get_wait_offset(self, channel: int | None = None, preset: str | None = None) -> float:
        return self._query_channel_float(channel, "WAIT:OFFS", preset)

    def set_wait_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "WAIT:STAT", enabled)

    def is_wait_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "WAIT:STAT")

    # Internal helpers ------------------------------------------------------
    def _format_function_list(self, functions: Sequence[str]) -> str:
        if not functions:
            raise ValueError("At least one measurement function is required")
        formatted = []
        for func in functions:
            formatted.append(f'"{func.strip().upper()}"')
        return ",".join(formatted)

    def _parse_function_list(self, response: str) -> list[str]:
        if not response.strip():
            return []
        tokens = []
        for part in response.split(","):
            tokens.append(part.strip().strip('"'))
        return tokens


class _OutputSubsystem(_ChannelSubsystem):
    """Output control (:OUTPut)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "OUTP")

    # Filter configuration ---------------------------------------------------
    def set_auto_filter_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "FILT:AUTO", enabled)

    def is_auto_filter_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "FILT:AUTO")

    def set_filter_frequency(
        self, frequency_hz: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "FILT:LPAS:FREQ", frequency_hz)

    def get_filter_frequency(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "FILT:LPAS:FREQ", preset)

    def set_filter_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "FILT:LPAS:STAT", enabled)

    def is_filter_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "FILT:LPAS:STAT")

    def set_filter_time_constant(
        self, time_constant_s: Scalar, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "FILT:LPAS:TCON", time_constant_s)

    def get_filter_time_constant(
        self, channel: int | None = None, preset: str | None = None
    ) -> float:
        return self._query_channel_float(channel, "FILT:LPAS:TCON", preset)

    def set_high_capacitance_mode(self, mode: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "HCAP:STAT", mode)

    def is_high_capacitance_mode(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "HCAP:STAT")

    def set_low_output_state(self, low_state: Scalar, channel: int | None = None) -> None:
        self._write_channel_value(channel, "LOW", low_state)

    def get_low_output_state(self, channel: int | None = None) -> float:
        return self._query_channel_float(channel, "LOW")

    def set_auto_output_off_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "OFF:AUTO", enabled)

    def is_auto_output_off_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "OFF:AUTO")

    def set_output_off_mode(self, mode: str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "OFF:MODE", mode)

    def get_output_off_mode(self, channel: int | None = None) -> str:
        return self._query_channel_value(channel, "OFF:MODE")

    def set_auto_output_on_enabled(
        self, enabled: bool | str, channel: int | None = None
    ) -> None:
        self._write_channel_value(channel, "ON:AUTO", enabled)

    def is_auto_output_on_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "ON:AUTO")

    def set_protection_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "PROT:STAT", enabled)

    def is_protection_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "PROT:STAT")

    def recall_setup(self, index: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "RECall", index)

    def save_setup(self, index: int, channel: int | None = None) -> None:
        self._write_channel_value(channel, "SAVE", index)

    def set_output_enabled(self, enabled: bool | str, channel: int | None = None) -> None:
        self._write_channel_value(channel, "STAT", enabled)

    def is_output_enabled(self, channel: int | None = None) -> bool:
        return self._query_channel_bool(channel, "STAT")


class _FormatState:
    """Track :FORMat settings to parse measurement responses correctly."""

    def __init__(self, driver: SCPIDriver) -> None:
        self._driver = driver
        self._data_format: str | None = None
        self._sense_elements: tuple[str, ...] = ()

    def ensure_ascii(self) -> None:
        self.ensure_data_format("ASC")

    def ensure_data_format(self, mode: str) -> None:
        normalized = mode.strip().upper()
        if normalized != self._data_format:
            self._driver.write(f":FORM:DATA {normalized}")
            self._data_format = normalized

    def ensure_elements(self, elements: Sequence[str]) -> None:
        normalized = tuple(element.strip().upper() for element in elements)
        if normalized != self._sense_elements:
            joined = ",".join(normalized)
            self._driver.write(f":FORM:ELEM:SENS {joined}")
            self._sense_elements = normalized

    def parse_measurements(self, response: str) -> list[MeasurementRecord]:
        if not self._sense_elements:
            raise RuntimeError("Measurement elements are not configured")
        parts = [part.strip() for part in response.split(",") if part.strip()]
        record_size = len(self._sense_elements)
        if record_size == 0:
            raise RuntimeError("No measurement elements to parse")
        if len(parts) % record_size != 0:
            raise RuntimeError(
                f"Unexpected measurement payload: {response!r} "
                f"does not align with {record_size} elements"
            )
        records: list[MeasurementRecord] = []
        for offset in range(0, len(parts), record_size):
            values = {
                element: float(parts[offset + idx])
                for idx, element in enumerate(self._sense_elements)
            }
            records.append(MeasurementRecord(values))
        return records


class _TriggerSubsystem(_NamespacedSubsystem):
    """Trigger routing (:TRIGger)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "TRIG")


class _LXISubsystem(_NamespacedSubsystem):
    """LXI-specific functionality (:LXI)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "LXI")


class _FetchSubsystem(_NamespacedSubsystem):
    """Trace/buffer fetch helpers (:FETCh)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "FETC")


class _FormatSubsystem(_NamespacedSubsystem):
    """Data format selection (:FORMat)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "FORM")

    def set_data_format(self, mode: str) -> None:
        self._driver.write(f"{self._command('DATA')} {mode}")

    def get_data_format(self) -> str:
        return self._driver.query(f"{self._command('DATA?')}")

    def set_data_format_ascii(self) -> None:
        self.set_data_format("ASC")

    def set_sense_elements(self, elements: Sequence[str]) -> None:
        if not elements:
            raise ValueError("At least one sense element is required")
        joined = ",".join(elem.strip().upper() for elem in elements)
        self._driver.write(f"{self._command('ELEM:SENS')} {joined}")

    def get_sense_elements(self) -> list[str]:
        response = self._driver.query(f"{self._command('ELEM:SENS?')}")
        if not response.strip():
            return []
        return [part.strip().upper() for part in response.split(",")]


class _ReadSubsystem(_NamespacedSubsystem):
    """Blocking read/measure commands (:READ)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "READ")


class _MeasureSubsystem(_NamespacedSubsystem):
    """Spot measurement helpers (:MEASure)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "MEAS")


class _CalculateSubsystem(_NamespacedSubsystem):
    """Limit test, math, statistics (:CALCulate)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "CALC")


class _TraceSubsystem(_NamespacedSubsystem):
    """Trace buffer configuration (:TRACe)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "TRAC")


class _HardcopySubsystem(_NamespacedSubsystem):
    """Screen capture / hard copy (:HCOPy)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "HCOP")


class _DisplaySubsystem(_NamespacedSubsystem):
    """Front panel display configuration (:DISPlay)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "DISP")


class _MemorySubsystem(_NamespacedSubsystem):
    """Mass memory / file handling (:MMEMory)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "MMEM")


class _ProgramSubsystem(_NamespacedSubsystem):
    """Program memory (:PROGram)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "PROG")


class _StatusSubsystem(_NamespacedSubsystem):
    """Status register helpers (:STATus)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "STAT")
