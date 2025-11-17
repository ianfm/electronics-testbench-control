from __future__ import annotations

from abc import ABC, abstractmethod

from testbench.core.scpi import SCPIDriver, SCPISettings


class DataAcquisitionUnit(ABC):
    def __init__(self, driver: SCPIDriver) -> None:
        self.driver = driver

    @abstractmethod
    def identify(self) -> str:
        ...

    @abstractmethod
    def read_channel(self, channel: int) -> float:
        ...

    def online(self) -> bool:
        return self.driver.resource is not None


class ScpiDAQ(DataAcquisitionUnit):
    ID_SUBSTRINGS: tuple[str, ...] = ()

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        driver = SCPIDriver(settings)
        self.instrument_name = driver.connect_first(self.ID_SUBSTRINGS)
        super().__init__(driver)

    def identify(self) -> str:
        return self.driver.query("*IDN?")

    def read_channel(self, channel: int) -> float:
        return float(self.driver.query(f"MEAS:VOLT:DC? (@{channel})", cast=float))
