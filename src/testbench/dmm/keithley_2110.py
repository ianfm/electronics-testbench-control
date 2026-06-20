from __future__ import annotations

import time
from typing import Tuple

from testbench.core.scpi import SCPISettings
from testbench.domains.dmm import ScpiDMM


class Keithley2110(ScpiDMM):
    """Keithley 2110 DMM implementation."""

    ID_SUBSTRINGS = ("2110",)

    def __init__(
        self,
        settings: SCPISettings = SCPISettings(),
        resource_name: str | None = None,
    ) -> None:
        super().__init__(settings, resource_name=resource_name)

    def get_configuration(self) -> Tuple[str, float, float]:
        conf = self.driver.query("CONF?")
        conf = conf[1 : len(conf) - 2]
        mode = conf[: conf.index(" ")]
        rng = float(conf[conf.index(" ") + 2 : conf.index(",")])
        resolution = float(conf[conf.index(",") + 1 :])
        return mode, resolution, rng

    def set_num_samples(self, num_samples: int) -> None:
        self.driver.write(f"SAMP:COUNT {num_samples}")
        self.driver.query("SAMP:COUNT?", cast=float)

    def reset(self) -> None:
        if not self.online():
            return
        self.driver.write("*RST")
        time.sleep(self.driver.settings.query_delay)
