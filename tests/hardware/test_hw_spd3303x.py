from __future__ import annotations

import unittest
import time

from testbench.binding import bind_driver, matching_device
from testbench.discovery import discover_instruments
from testbench.psu.siglent_spd3303x import SiglentSPD3303X


class SiglentSPD3303XHardwareTest(unittest.TestCase):
    """Hardware unit test for the Siglent SPD3303X-E power supply.

    Note: This test runs operations on physical hardware. It changes voltage and
    current setpoints and toggles output states. For safety, voltage setpoints are limited
    to a low level (0.5 V) and both channel outputs are explicitly turned off during tearDown.
    """

    @classmethod
    def setUpClass(cls) -> None:
        report = discover_instruments()
        device = matching_device("siglent-spd3303x", report)
        if device is None:
            raise unittest.SkipTest("Siglent SPD3303X hardware not found on bench; skipping.")

        cls.psu = bind_driver("siglent-spd3303x", device)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "psu"):
            # Ensure outputs are turned off for safety
            try:
                cls.psu.output_off(1)
            except Exception:
                pass
            try:
                cls.psu.output_off(2)
            except Exception:
                pass
            try:
                cls.psu.driver.close()
            except Exception:
                pass

    def test_identify(self) -> None:
        identity = self.psu.identify()
        self.assertIn("Siglent", identity)
        self.assertIn("SPD3303X", identity)

    def test_set_and_get_voltage(self) -> None:
        channel = 1
        safe_voltage = 0.5

        # Set and verify
        self.psu.set_voltage(channel, safe_voltage)
        time.sleep(0.5)
        measured = self.psu.get_voltage(channel)

        self.assertIsNotNone(measured)
        # Check that it is within 5% or 0.05V of target
        self.assertAlmostEqual(measured, safe_voltage, delta=0.05)

    def test_set_and_get_current(self) -> None:
        channel = 1
        safe_current = 0.05

        # Set and verify
        self.psu.set_current(channel, safe_current)
        time.sleep(0.5)
        measured = self.psu.get_current(channel)

        self.assertIsNotNone(measured)
        self.assertAlmostEqual(measured, safe_current, delta=0.01)

    def test_output_state_toggling(self) -> None:
        channel = 1
        # Toggle output on and off to verify no command errors are thrown
        self.psu.output_on(channel)
        time.sleep(0.25)
        self.psu.output_off(channel)


if __name__ == "__main__":
    unittest.main()
