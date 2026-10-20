from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from testbench.discovery import (
    DeviceIdentity,
    DiscoveryConfig,
    DiscoveryReport,
    discover_instruments,
)
from testbench.discovery.service import _load_cached_resources, _save_cache


class FakeInstrument:
    def __init__(self, response: str) -> None:
        self.response = response
        self.closed = False

    def query(self, command: str) -> str:
        if command != "*IDN?":
            raise AssertionError(f"unexpected command: {command}")
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeResourceManager:
    def __init__(self, responses: dict[str, str], enumerated=()) -> None:
        self.responses = responses
        self.enumerated = tuple(enumerated)

    def list_resources(self, query: str = ""):
        return self.enumerated

    def open_resource(self, resource: str, **kwargs):
        if resource not in self.responses:
            raise OSError("not reachable")
        return FakeInstrument(self.responses[resource])


class DeviceIdentityTests(unittest.TestCase):
    def test_parse_preserves_vendor_specific_firmware_fields(self) -> None:
        identity = DeviceIdentity.parse(
            "Siglent Technologies,SPD3303X-E,SPD3XJGQ903866,1.01.01.03.10,V6.2\n"
        )
        self.assertEqual(identity.manufacturer, "Siglent Technologies")
        self.assertEqual(identity.model, "SPD3303X-E")
        self.assertEqual(identity.serial, "SPD3XJGQ903866")
        self.assertEqual(identity.firmware, "1.01.01.03.10,V6.2")

    def test_empty_response_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DeviceIdentity.parse("\n")


class DiscoveryReportTests(unittest.TestCase):
    def test_same_serial_merges_multiple_transports(self) -> None:
        report = DiscoveryReport()
        identity = DeviceIdentity.parse("Vendor,Model,SERIAL,1.0")
        report.record("TCPIP::host::INSTR", "vxi11", identity=identity)
        report.record("TCPIP::host::5025::SOCKET", "socket", identity=identity)

        devices = report.identified_devices
        self.assertEqual(len(devices), 1)
        self.assertEqual(len(devices[0].resources), 2)


class LayeredDiscoveryTests(unittest.TestCase):
    def test_subnet_fallback_finds_resource_missing_from_visa_list(self) -> None:
        usb = "USB0::1::2::SERIAL::0::INSTR"
        vxi = "TCPIP::192.0.2.10::INSTR"
        socket_resource = "TCPIP::192.0.2.10::5025::SOCKET"
        rm = FakeResourceManager(
            {
                usb: "Vendor,DMM,DMM1,1.0",
                vxi: "Vendor,PSU,PSU1,2.0",
                socket_resource: "Vendor,PSU,PSU1,2.0",
            },
            enumerated=(usb,),
        )
        config = DiscoveryConfig(
            use_cache=False,
            subnets=("192.0.2.0/30",),
            query_attempts=1,
        )

        with patch(
            "testbench.discovery.service._scan_open_ports",
            return_value=[("192.0.2.10", 111), ("192.0.2.10", 5025)],
        ):
            report = discover_instruments(config, resource_manager=rm)

        self.assertEqual(len(report.identified_devices), 2)
        psu = next(
            device for device in report.identified_devices if device.identity.model == "PSU"
        )
        self.assertEqual(psu.resources, [vxi, socket_resource])

    def test_cache_contains_only_successfully_identified_resources(self) -> None:
        report = DiscoveryReport()
        report.record(
            "TCPIP::192.0.2.1::INSTR",
            "visa",
            identity=DeviceIdentity.parse("Vendor,Model,SERIAL,1.0"),
        )
        report.record("TCPIP::192.0.2.2::INSTR", "visa", error="timeout")

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "cache.json"
            _save_cache(path, report)
            loaded = _load_cached_resources(path, DiscoveryReport())

        self.assertEqual(loaded, ["TCPIP::192.0.2.1::INSTR"])

    def test_discover_instruments_uses_cache_fast_path(self) -> None:
        cached_resource = "TCPIP::192.0.2.1::INSTR"
        rm = FakeResourceManager(
            {
                cached_resource: "Vendor,Model,SERIAL,1.0",
            },
            enumerated=(),
        )
        
        with tempfile.TemporaryDirectory() as tempdir:
            cache_path = Path(tempdir) / "cache.json"
            report = DiscoveryReport()
            report.record(
                cached_resource,
                "visa",
                identity=DeviceIdentity.parse("Vendor,Model,SERIAL,1.0"),
            )
            _save_cache(cache_path, report)
            
            config = DiscoveryConfig(
                use_cache=True,
                cache_path=cache_path,
                scan_network=True,
                query_attempts=1,
            )
            
            with patch("testbench.discovery.service._scan_open_ports", side_effect=RuntimeError("Should not scan subnet!")):
                result_report = discover_instruments(config, resource_manager=rm)
                
            self.assertEqual(len(result_report.identified_devices), 1)
            self.assertEqual(result_report.identified_devices[0].identity.model, "Model")


if __name__ == "__main__":
    unittest.main()
