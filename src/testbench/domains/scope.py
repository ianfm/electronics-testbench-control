from __future__ import annotations

from abc import ABC, abstractmethod

from testbench.core.scpi import SCPIDriver, SCPISettings


class Oscilloscope(ABC):
    def __init__(self, driver: SCPIDriver) -> None:
        self.driver = driver

    @abstractmethod
    def identify(self) -> str:
        ...

    @abstractmethod
    def capture_waveform(self, channel: int) -> bytes:
        ...

    def online(self) -> bool:
        return self.driver.resource is not None


class ScpiScope(Oscilloscope):
    ID_SUBSTRINGS: tuple[str, ...] = ()

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        driver = SCPIDriver(settings)
        self.instrument_name = driver.connect_first(self.ID_SUBSTRINGS)
        super().__init__(driver)

    def identify(self) -> str:
        return self.driver.query("*IDN?")

    def capture_waveform(self, channel: int) -> bytes:
        # Placeholder that can be extended per model.
        self.driver.write(f":WAV:SOUR CHAN{channel}")
        self.driver.write(":WAV:DATA?")
        return self.driver.resource.read_raw(4096) if self.driver.resource else b""
