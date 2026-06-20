from __future__ import annotations

import time

from testbench.core.scpi import SCPISettings
from testbench.domains.psu import ScpiPSU


class SiglentSPD3303X(ScpiPSU):
    """Dual-channel Siglent supply."""

    ID_SUBSTRINGS = ("SPD3X",)

    def __init__(
        self,
        settings: SCPISettings = SCPISettings(),
        resource_name: str | None = None,
    ) -> None:
        super().__init__(settings, resource_name=resource_name)
        if self.online():
            self.driver.write("*RST")
            time.sleep(self.driver.settings.query_delay)

    def turn_on_display(self, channel: int = 1) -> None:
        self.driver.write(f"OUTP:WAVE CH{channel},ON")

    def turn_off_display(self, channel: int = 1) -> None:
        self.driver.write(f"OUTP:WAVE CH{channel},OFF")

    def get_voltage(self, channel: int = 1) -> float | None:
        """Query the actual measured output voltage of the channel."""
        try:
            return float(self.driver.query(f"MEASure:VOLTage? CH{channel}", cast=float))
        except Exception:
            return None

    def get_current(self, channel: int = 1) -> float | None:
        """Query the actual measured output current of the channel."""
        try:
            return float(self.driver.query(f"MEASure:CURRent? CH{channel}", cast=float))
        except Exception:
            return None
