from __future__ import annotations

import time
from typing import List, Optional

from testbench.core.scpi import SCPIDriver, SCPISettings
from testbench.domains.dmm import DigitalMultimeter, ScpiDMM


class Keysight34461A(ScpiDMM):
    """Keysight 34461A Truevolt DMM implementation.

    Beyond the base ``measure_*`` helpers this adds a configure-once /
    read-many pattern: ``configure_voltage_dc()`` programs a standing DCV
    configuration and ``read()`` triggers it with ``READ?``.  The base
    ``measure_voltage_dc()`` uses ``MEAS?`` which reconfigures the instrument
    on every call; ``read()`` is the loop-friendly path for long logging runs.
    """

    # connect_first matches against the VISA resource string, which for USB
    # carries decimal vendor/product IDs, not the model name:
    # USB0::10893::4865::<serial>::0::INSTR (0x2A8D Keysight, 0x1301 34461A).
    ID_SUBSTRINGS = ("10893", "4865")

    def __init__(
        self, settings: SCPISettings = SCPISettings(), resource_name: Optional[str] = None
    ) -> None:
        # ScpiDMM only supports discovery via connect_first; mirror the
        # sourcemeter base and connect directly when a resource is given.
        if resource_name:
            driver = SCPIDriver(settings)
            self.instrument_name = driver.connect_resource(resource_name)
            DigitalMultimeter.__init__(self, driver)
        else:
            super().__init__(settings)

    def configure_voltage_dc(
        self, range_v: float = 10.0, nplc: float = 1.0, high_impedance: bool = True
    ) -> None:
        """Program a standing DCV configuration for repeated ``read()`` calls.

        ``high_impedance=True`` enables auto input impedance (>10 GOhm on the
        100 mV/1 V/10 V ranges instead of the fixed 10 MOhm divider) -- the
        right choice when monitoring a battery directly.
        """
        self.driver.write(f"CONF:VOLT:DC {range_v}")
        self.driver.write(f"VOLT:DC:NPLC {nplc}")
        self.driver.write(f"VOLT:DC:IMP:AUTO {'ON' if high_impedance else 'OFF'}")
        self.driver.write("TRIG:SOUR IMM")
        self.driver.write("SAMP:COUN 1")

    def read(self) -> float:
        """Trigger the standing configuration and return one reading.

        Uses ``READ?`` so repeated calls do not reconfigure the instrument
        (unlike the base class ``measure_voltage_dc()``, which issues ``MEAS?``).
        """
        return float(self.driver.query("READ?", cast=float))

    def set_display_text(self, text: str) -> None:
        """Show a message on the front panel (useful during long runs)."""
        cleaned = text.replace('"', "'")
        self.driver.write(f'DISP:TEXT "{cleaned}"')

    def clear_display(self) -> None:
        self.driver.write("DISP:TEXT:CLE")

    def error_codes(self) -> List[int]:
        """Drain the SYST:ERR? queue and return the non-zero error codes."""
        codes: List[int] = []
        for _ in range(50):  # queue holds at most 20 errors; guard regardless
            response = self.driver.query("SYST:ERR?")
            code = int(response.split(",", 1)[0])
            if code == 0:
                break
            codes.append(code)
        return codes

    def reset(self) -> None:
        if not self.online():
            return
        self.driver.write("*RST")
        time.sleep(self.driver.settings.query_delay)
