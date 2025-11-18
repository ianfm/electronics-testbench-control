from __future__ import annotations

import time

from testbench.core.scpi import SCPISettings
from testbench.domains.psu import ScpiPSU


class SiglentSPD1305X(ScpiPSU):
    """Single-channel Siglent supply."""

    ID_SUBSTRINGS = ("SPD13",)

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        super().__init__(settings)
        if self.online():
            self.driver.write("*RST")
            time.sleep(self.driver.settings.query_delay)

    def turn_on_display(self, channel: int = 1) -> None:
        self.driver.write(f"OUTP:WAVE CH{channel},ON")

    def turn_off_display(self, channel: int = 1) -> None:
        self.driver.write(f"OUTP:WAVE CH{channel},OFF")

    def get_voltage(self, channel: int = 1) -> float | None:
        res = self.driver.resource
        if res is None:
            return None
        try:
            res.write(f"CH{channel}:VOLT?")
            time.sleep(self.driver.settings.query_delay)
            raw = res.read_raw(1024).decode("utf-8", errors="ignore").strip()
            return float(raw)
        except Exception:
            return None

    def get_current(self, channel: int = 1) -> float | None:
        res = self.driver.resource
        if res is None:
            return None
        try:
            res.write(f"CH{channel}:CURR?")
            time.sleep(self.driver.settings.query_delay)
            raw = res.read_raw(1024).decode("utf-8", errors="ignore").strip()
            return float(raw)
        except Exception:
            return None

    def set_voltage(self, channel: int, volts: float) -> None:
        # For this model, skip read-back verification to avoid timeouts.
        self.driver.write(f"CH{channel}:VOLT {volts}")

    def set_current(self, channel: int, amps: float) -> None:
        self.driver.write(f"CH{channel}:CURR {amps}")
