from __future__ import annotations

import time
from typing import Optional

from testbench.core.scpi import SCPISettings
from testbench.domains.dmm import ScpiDMM


class RigolDM3058E(ScpiDMM):
    """Rigol DM3058E implementation."""

    ID_SUBSTRINGS = ("DM3",)

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        super().__init__(settings)

    def get_command_set(self) -> Optional[str]:
        if not self.online():
            return None
        return self.driver.query("CMDSET?")

    # The base ScpiDMM already supplies the standard MEAS commands, but Rigol
    # also supports returning the reported config if needed.
    def measure_voltage_dc(self) -> float:
        # Rigol can be sensitive to spacing; use the explicit MEAS form.
        return float(self.driver.query("MEAS:VOLT:DC?", cast=float))

    def measure_current_dc(self) -> float:
        return float(self.driver.query("MEAS:CURR:DC?", cast=float))

    def measure_resistance(self) -> float:
        return float(self.driver.query("MEAS:RES?", cast=float))

    def reset(self) -> None:
        if not self.online():
            return
        self.driver.write("*RST")
        time.sleep(self.driver.settings.query_delay)
