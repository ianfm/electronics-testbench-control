from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from testbench.core.scpi import SCPISettings
from testbench.domains.daq import ScpiDAQ


@dataclass(frozen=True)
class M300ModuleIdentity:
    manufacturer: str
    model: str
    serial: str
    firmware: str

    @property
    def installed(self) -> bool:
        return self.model != "0"


class RigolM300(ScpiDAQ):
    """Rigol M300 DAQ/switch mainframe using its documented SCPI interface."""

    ID_SUBSTRINGS = ("M300",)
    SLOTS = (100, 200, 300, 400, 500)

    def __init__(
        self,
        settings: SCPISettings = SCPISettings(),
        resource_name: str | None = None,
    ) -> None:
        super().__init__(settings, resource_name=resource_name)

    def module_identity(self, slot: int) -> M300ModuleIdentity:
        self._validate_slot(slot)
        response = self.driver.query(f"SYST:CTYP? {slot}")
        parts = [part.strip() for part in response.split(",")]
        if len(parts) != 4:
            raise RuntimeError(f"Unexpected M300 module identity: {response!r}")
        return M300ModuleIdentity(*parts)

    def module_inventory(self) -> dict[int, M300ModuleIdentity]:
        return {slot: self.module_identity(slot) for slot in self.SLOTS}

    def dmm_installed(self) -> bool:
        return bool(int(self.driver.query("INST:DMM:INST?", cast=int)))

    def dmm_enabled(self) -> bool:
        return bool(int(self.driver.query("INST:DMM?", cast=int)))

    def read_channel(self, channel: int) -> float:
        return self.measure_voltage_dc(channel)

    def measure_voltage_dc(
        self,
        channel: int,
        measurement_range: str | float = "AUTO",
        resolution: str | float = "DEF",
    ) -> float:
        """Scan one multiplexer channel for DC voltage and clear the scan list.

        This follows the M300 Programming Guide sequence: ``CONF:VOLT:DC``,
        ``ROUT:SCAN``, immediate trigger, and ``READ?``. Clearing the scan list
        after the reading leaves the multiplexer relay open.
        """
        self._validate_channel(channel)
        if not self.dmm_installed():
            raise RuntimeError("M300 DMM module is not installed")
        if not self.dmm_enabled():
            raise RuntimeError("M300 DMM module is disabled")

        self.driver.write(
            f"CONF:VOLT:DC {measurement_range},{resolution},(@{channel})"
        )
        self.driver.write(f"ROUT:SCAN (@{channel})")
        self.driver.write("TRIG:SOUR IMM")
        try:
            return float(self.driver.query("READ?", cast=float))
        finally:
            self.driver.write("ROUT:SCAN (@)")

    def setup_scan(
        self,
        channels: Iterable[int],
        measurement_range: str | float = "AUTO",
        resolution: str | float = "DEF",
    ) -> None:
        """Configure a list of channels for DC voltage measurements and set up the scan.

        This sets the measurement parameters and target scan list, then configures
        the trigger source to immediate.
        """
        ch_list = list(channels)
        for ch in ch_list:
            self._validate_channel(ch)
        if not self.dmm_installed():
            raise RuntimeError("M300 DMM module is not installed")
        if not self.dmm_enabled():
            raise RuntimeError("M300 DMM module is disabled")

        ch_list_str = self.driver.format_channel_list(ch_list)
        self.driver.write(f"CONF:VOLT:DC {measurement_range},{resolution},{ch_list_str}")
        self.driver.write(f"ROUT:SCAN {ch_list_str}")
        self.driver.write("TRIG:SOUR IMM")

    def read_scan(self) -> list[float]:
        """Trigger the configured scan and return the readings for all channels in the scan list."""
        if not self.dmm_installed():
            raise RuntimeError("M300 DMM module is not installed")
        if not self.dmm_enabled():
            raise RuntimeError("M300 DMM module is disabled")
        return self.driver.query_float_list("READ?")

    def clear_scan(self) -> None:
        """Clear the active scan list to safely open all multiplexer relays."""
        self.driver.write("ROUT:SCAN (@)")

    def channel_is_open(self, channel: int) -> bool:
        self._validate_channel(channel)
        return bool(int(self.driver.query(f"ROUT:OPEN? (@{channel})", cast=int)))

    def channel_is_closed(self, channel: int) -> bool:
        self._validate_channel(channel)
        return bool(int(self.driver.query(f"ROUT:CLOSE? (@{channel})", cast=int)))

    def close_channel(self, channel: int) -> None:
        self._validate_channel(channel)
        self.driver.write(f"ROUT:CLOSE (@{channel})")

    def open_channel(self, channel: int) -> None:
        self._validate_channel(channel)
        self.driver.write(f"ROUT:OPEN (@{channel})")

    def close_channels(self, channels: Iterable[int]) -> None:
        ch_list = list(channels)
        for ch in ch_list:
            self._validate_channel(ch)
        if not ch_list:
            return
        ch_list_str = self.driver.format_channel_list(ch_list)
        self.driver.write(f"ROUT:CLOSE {ch_list_str}")

    def open_channels(self, channels: Iterable[int]) -> None:
        ch_list = list(channels)
        for ch in ch_list:
            self._validate_channel(ch)
        if not ch_list:
            return
        ch_list_str = self.driver.format_channel_list(ch_list)
        self.driver.write(f"ROUT:OPEN {ch_list_str}")

    @classmethod
    def _validate_slot(cls, slot: int) -> None:
        if slot not in cls.SLOTS:
            raise ValueError(f"slot must be one of {cls.SLOTS}")

    @staticmethod
    def _validate_channel(channel: int) -> None:
        slot = channel // 100
        position = channel % 100
        if slot not in range(1, 6) or position not in range(1, 100):
            raise ValueError("channel must use M300 SCC notation (for example, 201)")
