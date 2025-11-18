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

    @abstractmethod
    def get_voltage(self, channel: int) -> float | None:
        ...

    @abstractmethod
    def get_current(self, channel: int) -> float | None:
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
        try:
            self.driver.query(f"CH{channel}:VOLT?", cast=float)
        except Exception:
            # Some models (e.g., SPD1305X) do not respond reliably to queries here;
            # treat timeouts as non-fatal since the write has already been sent.
            print("No response; ignoring")
            pass

    def set_current(self, channel: int, amps: float) -> None:
        self.driver.write(f"CH{channel}:CURR {amps}")
        try:
            self.driver.query(f"CH{channel}:CURR?", cast=float)
        except Exception:
            print("No response; ignoring")
            pass

    def output_on(self, channel: int) -> None:
        self.driver.write(f"OUTP CH{channel},ON")

    def output_off(self, channel: int) -> None:
        self.driver.write(f"OUTP CH{channel},OFF")

    def get_voltage(self, channel: int = 1) -> float | None:
        try:
            return float(self.driver.query(f"CH{channel}:VOLT?", cast=float))
        except Exception:
            return None

    def get_current(self, channel: int = 1) -> float | None:
        try:
            return float(self.driver.query(f"CH{channel}:CURR?", cast=float))
        except Exception:
            return None
