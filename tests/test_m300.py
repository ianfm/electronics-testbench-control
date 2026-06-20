from __future__ import annotations

import unittest
from unittest.mock import patch

from testbench.daq.rigol_m300 import RigolM300


class FakeM300Driver:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.resource = None
        self.writes: list[str] = []
        self.responses = {
            "INST:DMM:INST?": "1",
            "INST:DMM?": "1",
            "READ?": "+7.910755833E-02",
            "ROUT:OPEN? (@201)": "1",
            "SYST:CTYP? 100": "RIGOL TECHNOLOGIES,MC3065,SERIAL,01.02.03.04",
        }

    def connect_resource(self, resource_name: str) -> str:
        self.resource = object()
        return resource_name

    def connect_first(self, substrings):
        return None

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str, cast=None):
        self.writes.append(command)
        response = self.responses[command]
        return cast(response) if cast else response

    def query_float_list(self, command: str) -> list[float]:
        response = self.query(command)
        return [float(part) for part in response.split(",") if part.strip()]

    @staticmethod
    def format_channel_list(channels) -> str:
        inner = ",".join(str(ch) for ch in channels)
        return f"(@{inner})"


class RigolM300Tests(unittest.TestCase):
    def make_instrument(self) -> tuple[RigolM300, FakeM300Driver]:
        with patch("testbench.domains.daq.SCPIDriver", FakeM300Driver):
            instrument = RigolM300(resource_name="TCPIP::192.0.2.10::INSTR")
        return instrument, instrument.driver

    def test_module_identity_parsing(self) -> None:
        instrument, _ = self.make_instrument()
        module = instrument.module_identity(100)
        self.assertEqual(module.model, "MC3065")
        self.assertEqual(module.serial, "SERIAL")
        self.assertTrue(module.installed)

    def test_voltage_measurement_clears_scan_list(self) -> None:
        instrument, driver = self.make_instrument()
        reading = instrument.measure_voltage_dc(201)

        self.assertAlmostEqual(reading, 0.07910755833)
        self.assertEqual(
            driver.writes,
            [
                "INST:DMM:INST?",
                "INST:DMM?",
                "CONF:VOLT:DC AUTO,DEF,(@201)",
                "ROUT:SCAN (@201)",
                "TRIG:SOUR IMM",
                "READ?",
                "ROUT:SCAN (@)",
            ],
        )
        self.assertTrue(instrument.channel_is_open(201))

    def test_multichannel_scan_lifecycle(self) -> None:
        instrument, driver = self.make_instrument()
        driver.responses["READ?"] = "+1.0,+2.0,+3.0"

        channels = [201, 202, 203]
        instrument.setup_scan(channels)
        self.assertEqual(
            driver.writes,
            [
                "INST:DMM:INST?",
                "INST:DMM?",
                "CONF:VOLT:DC AUTO,DEF,(@201,202,203)",
                "ROUT:SCAN (@201,202,203)",
                "TRIG:SOUR IMM",
            ]
        )

        driver.writes.clear()
        readings = instrument.read_scan()
        self.assertEqual(readings, [1.0, 2.0, 3.0])
        self.assertEqual(driver.writes, ["INST:DMM:INST?", "INST:DMM?", "READ?"])

        driver.writes.clear()
        instrument.clear_scan()
        self.assertEqual(driver.writes, ["ROUT:SCAN (@)"])

    def test_invalid_channel_is_rejected_without_io(self) -> None:
        instrument, driver = self.make_instrument()
        with self.assertRaises(ValueError):
            instrument.measure_voltage_dc(99)
        self.assertEqual(driver.writes, [])


if __name__ == "__main__":
    unittest.main()
