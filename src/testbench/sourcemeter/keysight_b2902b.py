from __future__ import annotations

import time

from testbench.core.scpi import SCPIDriver, SCPISettings
from testbench.domains.sourcemeter import ScpiSourceMeter


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

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        super().__init__(settings)
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


class _B2902BSubsystem:
    """Shared plumbing for subsystem helpers."""

    def __init__(self, driver: SCPIDriver) -> None:
        self._driver = driver

    @property
    def online(self) -> bool:
        return self._driver.resource is not None


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

    def set_current_mode(self, mode: str, channel: int | None = None) -> None:
        self._set_mode("CURR", mode, channel)

    def get_current_mode(self, channel: int | None = None) -> str:
        return self._get_mode("CURR", channel)

    def set_voltage_mode(self, mode: str, channel: int | None = None) -> None:
        self._set_mode("VOLT", mode, channel)

    def get_voltage_mode(self, channel: int | None = None) -> str:
        return self._get_mode("VOLT", channel)

    def _set_mode(self, function: str, mode: str, channel: int | None) -> None:
        scpi_mode = self._normalize_source_mode(mode)
        prefix = self._prefix(channel)
        self._driver.write(f"{prefix}:{function}:MODE {scpi_mode}")

    def _get_mode(self, function: str, channel: int | None) -> str:
        prefix = self._prefix(channel)
        return self._driver.query(f"{prefix}:{function}:MODE?")

    def _normalize_source_mode(self, mode: str) -> str:
        key = mode.strip().upper()
        scpi_mode = self._VALID_SOURCE_MODES.get(key)
        if scpi_mode is None:
            raise ValueError(
                f"Unsupported source mode {mode!r}. "
                "Valid modes: FIX, LIST, SWE (SWEEP)."
            )
        return scpi_mode


class _SenseSubsystem(_ChannelSubsystem):
    """Measurement configuration (:SENSe)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "SENS")


class _OutputSubsystem(_ChannelSubsystem):
    """Output control (:OUTPut)."""

    def __init__(self, driver: SCPIDriver) -> None:
        super().__init__(driver, "OUTP")


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
