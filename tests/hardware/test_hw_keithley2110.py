from __future__ import annotations

import unittest

from testbench.binding import bind_driver, matching_device
from testbench.discovery import discover_instruments
from testbench.dmm.keithley_2110 import Keithley2110


class Keithley2110HardwareTest(unittest.TestCase):
    """Hardware unit test for the Keithley 2110 DMM.

    Note: This test runs operations on physical hardware. It performs basic DC voltage
    and current measurements, but does not enable high power outputs or change external
    route matrices.
    """

    @classmethod
    def setUpClass(cls) -> None:
        # Run discovery to locate the device
        report = discover_instruments()
        device = matching_device("keithley-2110", report)
        if device is None:
            raise unittest.SkipTest("Keithley 2110 hardware not found on bench; skipping.")

        cls.dmm = bind_driver("keithley-2110", device)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "dmm"):
            try:
                cls.dmm.driver.close()
            except Exception:
                pass

    def test_identify(self) -> None:
        identity = self.dmm.identify()
        self.assertIn("KEITHLEY", identity)
        self.assertIn("2110", identity)

    def test_measure_voltage_dc(self) -> None:
        val = self.dmm.measure_voltage_dc()
        self.assertIsInstance(val, float)
        # Verify the reading is a plausible floating/open voltage or connection value
        self.assertTrue(-1000.0 < val < 1000.0)

    def test_measure_current_dc(self) -> None:
        val = self.dmm.measure_current_dc()
        self.assertIsInstance(val, float)
        self.assertTrue(-15.0 < val < 15.0)


if __name__ == "__main__":
    unittest.main()
