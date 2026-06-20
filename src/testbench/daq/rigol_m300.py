from __future__ import annotations

from dataclasses import dataclass

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

    def channel_is_open(self, channel: int) -> bool:
        self._validate_channel(channel)
        return bool(int(self.driver.query(f"ROUT:OPEN? (@{channel})", cast=int)))

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
