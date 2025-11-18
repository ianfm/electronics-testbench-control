from __future__ import annotations

import time

from testbench.core.scpi import SCPISettings
from testbench.domains.sourcemeter import ScpiSourceMeter


class KeysightB2902B(ScpiSourceMeter):
    """Keysight B2902B source/measure unit."""

    ID_SUBSTRINGS = ("B2902", "Keysight")

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        super().__init__(settings)
        if self.online():
            # Use remote mode and a known state.
            self.driver.write("SYST:REM")
            self.driver.write("*RST")
            time.sleep(self.driver.settings.query_delay)

    def set_voltage_source(self, volts: float) -> None:
        # Configure for voltage source mode.
        self.driver.write("SOUR:FUNC VOLT")
        self.driver.write(f"SOUR:VOLT {volts}")

    def measure_current(self) -> float:
        # Trigger a current measurement and return the result.
        return float(self.driver.query("MEAS:CURR?", cast=float))

    def output_on(self) -> None:
        self.driver.write("OUTP ON")

    def output_off(self) -> None:
        self.driver.write("OUTP OFF")
