from __future__ import annotations

import unittest

from testbench.binding import bind_driver, matching_device
from testbench.discovery import discover_instruments
from testbench.dmm.rigol_dm3058e import RigolDM3058E


class RigolDM3058EHardwareTest(unittest.TestCase):
    """Hardware unit test for the Rigol DM3058E DMM.

    Note: This test runs operations on physical hardware. It performs basic DC voltage
    and current measurements.
    """

    @classmethod
    def setUpClass(cls) -> None:
        report = discover_instruments()
        device = matching_device("rigol-dm3058e", report)
        if device is None:
            raise unittest.SkipTest("Rigol DM3058E hardware not found on bench; skipping.")

        cls.dmm = bind_driver("rigol-dm3058e", device)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "dmm"):
            try:
                cls.dmm.driver.close()
            except Exception:
                pass

    def test_identify(self) -> None:
        identity = self.dmm.identify()
        self.assertIn("Rigol", identity)
        self.assertIn("DM3058", identity)

    def test_measure_voltage_dc(self) -> None:
        val = self.dmm.measure_voltage_dc()
        self.assertIsInstance(val, float)
        self.assertTrue(-1000.0 < val < 1000.0)

    def test_measure_current_dc(self) -> None:
        val = self.dmm.measure_current_dc()
        self.assertIsInstance(val, float)
        self.assertTrue(-15.0 < val < 15.0)


if __name__ == "__main__":
    unittest.main()
