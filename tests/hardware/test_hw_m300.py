from __future__ import annotations

import unittest

from testbench.binding import bind_driver, matching_device
from testbench.daq.rigol_m300 import RigolM300
from testbench.discovery import discover_instruments


class RigolM300HardwareTest(unittest.TestCase):
    """Hardware unit test for the Rigol M300 DAQ mainframe.

    Note: This test runs operations on physical hardware. It queries module inventory,
    verifies DMM installation, measures a single channel, and runs a multi-channel scan lifecycle.
    Relays are cleared and opened in the tearDownClass.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import os
        resource = os.environ.get("TEST_HW_RESOURCE_M300")
        if resource:
            cls.daq = RigolM300(resource_name=resource)
        else:
            report = discover_instruments()
            device = matching_device("rigol-m300", report)
            if device is None:
                raise unittest.SkipTest("Rigol M300 hardware not found on bench; skipping.")
            cls.daq = bind_driver("rigol-m300", device)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "daq"):
            try:
                cls.daq.clear_scan()
            except Exception:
                pass
            try:
                cls.daq.driver.close()
            except Exception:
                pass

    def test_identify(self) -> None:
        identity = self.daq.identify()
        self.assertIn("RIGOL", identity)
        self.assertIn("M300", identity)

    def test_module_inventory(self) -> None:
        inventory = self.daq.module_inventory()
        self.assertTrue(len(inventory) > 0)
        # Verify that slot 100 has the MC3065 DMM card
        self.assertIn(100, inventory)
        self.assertEqual(inventory[100].model, "MC3065")

    def test_dmm_status(self) -> None:
        self.assertTrue(self.daq.dmm_installed())
        self.assertTrue(self.daq.dmm_enabled())

    def test_single_channel_measurement(self) -> None:
        # Measure voltage on channel 201 (on the slot 200 multiplexer card)
        val = self.daq.measure_voltage_dc(201)
        self.assertIsInstance(val, float)
        self.assertTrue(-300.0 < val < 300.0)
        # Single channel measurement should clear the scan list, leaving the channel open
        self.assertTrue(self.daq.channel_is_open(201))

    def test_multichannel_scan_workflow(self) -> None:
        channels = [201, 202, 203]
        try:
            self.daq.setup_scan(channels)
            readings = self.daq.read_scan()
            self.assertEqual(len(readings), 3)
            for val in readings:
                self.assertIsInstance(val, float)
        finally:
            self.daq.clear_scan()


if __name__ == "__main__":
    unittest.main()
