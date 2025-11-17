from __future__ import annotations

from abc import ABC, abstractmethod

from testbench.core.scpi import SCPIDriver, SCPISettings


class SourceMeter(ABC):
    def __init__(self, driver: SCPIDriver) -> None:
        self.driver = driver

    @abstractmethod
    def identify(self) -> str:
        ...

    @abstractmethod
    def set_voltage_source(self, volts: float) -> None:
        ...

    @abstractmethod
    def measure_current(self) -> float:
        ...

    def online(self) -> bool:
        return self.driver.resource is not None


class ScpiSourceMeter(SourceMeter):
    ID_SUBSTRINGS: tuple[str, ...] = ()

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        driver = SCPIDriver(settings)
        self.instrument_name = driver.connect_first(self.ID_SUBSTRINGS)
        super().__init__(driver)

    def identify(self) -> str:
        return self.driver.query("*IDN?")

    def set_voltage_source(self, volts: float) -> None:
        self.driver.write(f"SOUR:VOLT {volts}")
        self.driver.query("SOUR:VOLT?", cast=float)

    def measure_current(self) -> float:
        return float(self.driver.query("MEAS:CURR?", cast=float))
