from __future__ import annotations

import unittest
from unittest.mock import patch

from testbench.binding import bind_driver, matching_device
from testbench.discovery import DeviceIdentity, DiscoveredDevice, DiscoveryReport
from testbench.dmm.rigol_dm3058e import RigolDM3058E


class FakeSCPIDriver:
    instances = []

    def __init__(self, settings) -> None:
        self.settings = settings
        self.resource = None
        self.connected_resource = None
        self.independent_terms = None
        self.writes = []
        self.__class__.instances.append(self)

    def connect_resource(self, resource_name: str) -> str:
        self.connected_resource = resource_name
        self.resource = object()
        return resource_name

    def connect_first(self, substrings) -> str | None:
        self.independent_terms = tuple(substrings)
        return None

    def write(self, command: str) -> None:
        self.writes.append(command)


class ResourceInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSCPIDriver.instances.clear()

    def test_explicit_resource_bypasses_independent_lookup(self) -> None:
        resource = "USB0::6833::2500::DM3R232201369::0::INSTR"
        with patch("testbench.domains.dmm.SCPIDriver", FakeSCPIDriver):
            instrument = RigolDM3058E(resource_name=resource)

        self.assertTrue(instrument.online())
        driver = FakeSCPIDriver.instances[-1]
        self.assertEqual(driver.connected_resource, resource)
        self.assertIsNone(driver.independent_terms)

    def test_no_resource_preserves_independent_lookup(self) -> None:
        with patch("testbench.domains.dmm.SCPIDriver", FakeSCPIDriver):
            instrument = RigolDM3058E()

        self.assertFalse(instrument.online())
        driver = FakeSCPIDriver.instances[-1]
        self.assertEqual(driver.independent_terms, ("DM3",))


class TopLevelBindingTests(unittest.TestCase):
    def test_identity_match_handles_keithley_reported_model_name(self) -> None:
        device = DiscoveredDevice(
            DeviceIdentity.parse(
                "KEITHLEY INSTRUMENTS INC.,MODEL 2110,8009937,02.02-02-01"
            ),
            resources=["USB0::1510::8464::8009937::0::INSTR"],
        )
        report = DiscoveryReport(devices={device.identity.key: device})

        self.assertIs(matching_device("keithley-2110", report), device)

    def test_binding_prefers_vxi11_over_raw_socket(self) -> None:
        device = DiscoveredDevice(
            DeviceIdentity.parse(
                "Siglent Technologies,SPD3303X-E,SERIAL,1.0,V1"
            ),
            resources=[
                "TCPIP::192.0.2.10::5025::SOCKET",
                "TCPIP::192.0.2.10::INSTR",
            ],
        )
        with patch("testbench.domains.psu.SCPIDriver", FakeSCPIDriver):
            instrument = bind_driver("siglent-spd3303x", device)

        self.assertEqual(instrument.instrument_name, "TCPIP::192.0.2.10::INSTR")


if __name__ == "__main__":
    unittest.main()
