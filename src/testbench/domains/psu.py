from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Tuple

from testbench.core.scpi import SCPIDriver, SCPISettings


class PowerSupply(ABC):
    """Common interface for programmable power supplies."""

    def __init__(self, driver: SCPIDriver) -> None:
        self.driver = driver

    @abstractmethod
    def identify(self) -> str:
        ...

    @abstractmethod
    def set_voltage(self, channel: int, volts: float) -> None:
        ...

    @abstractmethod
    def set_current(self, channel: int, amps: float) -> None:
        ...

    @abstractmethod
    def output_on(self, channel: int) -> None:
        ...

    @abstractmethod
    def output_off(self, channel: int) -> None:
        ...

    def online(self) -> bool:
        return self.driver.resource is not None


class ScpiPSU(PowerSupply):
    """SCPI-backed default behavior for multi-channel PSUs."""

    ID_SUBSTRINGS: Tuple[str, ...] = ()

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        driver = SCPIDriver(settings)
        name = driver.connect_first(self.ID_SUBSTRINGS)
        self.instrument_name = name
        super().__init__(driver)

    def identify(self) -> str:
        return self.driver.query("*IDN?")

    def set_voltage(self, channel: int, volts: float) -> None:
        self.driver.write(f"CH{channel}:VOLT {volts}")
        self.driver.query(f"CH{channel}:VOLT?", cast=float)

    def set_current(self, channel: int, amps: float) -> None:
        self.driver.write(f"CH{channel}:CURR {amps}")
        self.driver.query(f"CH{channel}:CURR?", cast=float)

    def output_on(self, channel: int) -> None:
        self.driver.write(f"OUTP CH{channel},ON")

    def output_off(self, channel: int) -> None:
        self.driver.write(f"OUTP CH{channel},OFF")
