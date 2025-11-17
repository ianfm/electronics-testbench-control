from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple

from testbench.core.scpi import SCPIDriver, SCPISettings


class DigitalMultimeter(ABC):
    """Common interface for DMM instruments."""

    def __init__(self, driver: SCPIDriver) -> None:
        self.driver = driver

    @abstractmethod
    def identify(self) -> str:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...

    @abstractmethod
    def measure_voltage_dc(self) -> float:
        ...

    @abstractmethod
    def measure_current_dc(self) -> float:
        ...

    @abstractmethod
    def measure_resistance(self) -> float:
        ...

    def online(self) -> bool:
        return self.driver.resource is not None


class ScpiDMM(DigitalMultimeter):
    """SCPI-backed default behavior."""

    ID_SUBSTRINGS: Tuple[str, ...] = ()

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        driver = SCPIDriver(settings)
        name = driver.connect_first(self.ID_SUBSTRINGS)
        self.instrument_name = name
        super().__init__(driver)

    def identify(self) -> str:
        return self.driver.query("*IDN?")

    def reset(self) -> None:
        self.driver.write("*RST")

    def measure_voltage_dc(self) -> float:
        return float(self.driver.query("MEAS:VOLT:DC?", cast=float))

    def measure_current_dc(self) -> float:
        return float(self.driver.query("MEAS:CURR:DC?", cast=float))

    def measure_resistance(self) -> float:
        return float(self.driver.query("MEAS:RES?", cast=float))
