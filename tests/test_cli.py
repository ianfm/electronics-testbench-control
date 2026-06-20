from __future__ import annotations

import io
import unittest
from unittest.mock import patch, MagicMock
import argparse

from testbench.cli.main import main
from testbench.discovery import DiscoveryReport, DiscoveryAttempt, DeviceIdentity, DiscoveredDevice


class CLITests(unittest.TestCase):
    @patch("testbench.cli.main.discover_instruments")
    def test_discovery_warning_when_stale_usb_detected(self, mock_discover) -> None:
        # Create a report with a failed USB attempt
        report = DiscoveryReport()
        report.attempts.append(
            DiscoveryAttempt(
                resource="USB0::1510::8464::8009937::0::INSTR",
                source="visa",
                outcome="failed",
                error="Pipe error",
            )
        )
        mock_discover.return_value = report

        # Redirect stdout to capture prints
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["discover", "--no-network-scan"])

        output = mock_stdout.getvalue()
        self.assertIn("WARNING: One or more USB instruments failed to respond to discovery.", output)
        self.assertIn("Recommendation: Run 'testbench reset-usb' to clear locked USB endpoints.", output)
        # No devices were identified, so it should return 1
        self.assertEqual(exit_code, 1)

    @patch("testbench.cli.main.discover_instruments")
    def test_verify_warning_when_stale_usb_detected(self, mock_discover) -> None:
        # Create a report with a failed USB attempt
        report = DiscoveryReport()
        report.attempts.append(
            DiscoveryAttempt(
                resource="USB0::1510::8464::8009937::0::INSTR",
                source="visa",
                outcome="failed",
                error="Pipe error",
            )
        )
        mock_discover.return_value = report

        # Redirect stdout to capture prints
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["verify"])

        output = mock_stdout.getvalue()
        self.assertIn("WARNING: One or more USB instruments failed to respond to discovery.", output)
        self.assertIn("Recommendation: Run verification with the --reset-usb flag:", output)
        self.assertIn("testbench verify --reset-usb", output)
        # Failed verification should return 1
        self.assertEqual(exit_code, 1)

    @patch("testbench.cli.main.discover_instruments")
    def test_discovery_no_warning_when_no_failed_usb(self, mock_discover) -> None:
        # Create a report with a failed TCPIP attempt, but no USB attempts failed
        report = DiscoveryReport()
        report.attempts.append(
            DiscoveryAttempt(
                resource="TCPIP::192.168.86.170::INSTR",
                source="vxi11-subnet-scan",
                outcome="failed",
                error="Connection timed out",
            )
        )
        mock_discover.return_value = report

        # Redirect stdout to capture prints
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["discover", "--no-network-scan"])

        output = mock_stdout.getvalue()
        self.assertNotIn("WARNING: One or more USB instruments failed to respond to discovery.", output)
        self.assertEqual(exit_code, 1)

    @patch("testbench.cli.main.discover_instruments")
    @patch("testbench.cli.main.bind_driver")
    def test_dmm_omitted_model_single_match(self, mock_bind, mock_discover) -> None:
        # Create a report with a single identified DMM
        report = DiscoveryReport()
        identity = DeviceIdentity.parse("KEITHLEY INSTRUMENTS INC.,MODEL 2110,8009937,02.02-02-01")
        device = DiscoveredDevice(identity=identity, resources=["USB0::1510::8464::8009937::0::INSTR"])
        report.devices[identity.key] = device
        mock_discover.return_value = report

        # Mock the bound driver
        mock_driver = MagicMock()
        mock_driver.online.return_value = True
        mock_driver.measure_voltage_dc.return_value = 0.1234
        mock_bind.return_value = mock_driver

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["dmm", "measure", "voltage"])

        output = mock_stdout.getvalue()
        self.assertIn("Discovering connected DMM instruments...", output)
        self.assertIn("(Auto-discovered connected instrument: keithley-2110)", output)
        self.assertIn("123.400 mV", output)
        self.assertEqual(exit_code, 0)
        mock_bind.assert_called_once_with("keithley-2110", device)

    @patch("testbench.cli.main.discover_instruments")
    def test_dmm_omitted_model_multiple_matches(self, mock_discover) -> None:
        # Create a report with multiple identified DMMs
        report = DiscoveryReport()
        
        identity1 = DeviceIdentity.parse("KEITHLEY INSTRUMENTS INC.,MODEL 2110,8009937,02.02-02-01")
        device1 = DiscoveredDevice(identity=identity1, resources=["USB0::1510::8464::8009937::0::INSTR"])
        report.devices[identity1.key] = device1

        identity2 = DeviceIdentity.parse("Rigol Technologies,DM3058E,DM3R232201369,1.0")
        device2 = DiscoveredDevice(identity=identity2, resources=["USB0::6833::2500::DM3R232201369::0::INSTR"])
        report.devices[identity2.key] = device2
        
        mock_discover.return_value = report

        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                main(["dmm", "measure", "voltage"])

        err_output = mock_stderr.getvalue()
        self.assertIn("Error: Multiple DMM instruments discovered on the bench:", err_output)
        self.assertIn("keithley-2110 at USB0::1510::8464::8009937::0::INSTR", err_output)
        self.assertIn("rigol-dm3058e at USB0::6833::2500::DM3R232201369::0::INSTR", err_output)
        self.assertEqual(cm.exception.code, 1)

    @patch("testbench.cli.main.discover_instruments")
    def test_dmm_omitted_model_no_match(self, mock_discover) -> None:
        # Create a report with no identified devices
        report = DiscoveryReport()
        mock_discover.return_value = report

        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            with self.assertRaises(SystemExit) as cm:
                main(["dmm", "measure", "voltage"])

        err_output = mock_stderr.getvalue()
        self.assertIn("Error: No connected DMM instrument was discovered.", err_output)
        self.assertEqual(cm.exception.code, 1)

    @patch("testbench.cli.main._detect_model_from_resource")
    @patch("testbench.cli.main._connect_model")
    def test_dmm_omitted_model_with_resource(self, mock_connect, mock_detect) -> None:
        mock_detect.return_value = "keithley-2110"
        
        mock_driver = MagicMock()
        mock_driver.online.return_value = True
        mock_driver.measure_voltage_dc.return_value = 1.25
        mock_connect.return_value = mock_driver

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["dmm", "--resource", "USB0::1::2::3", "measure", "voltage"])

        output = mock_stdout.getvalue()
        self.assertIn("(Auto-detected model: keithley-2110)", output)
        self.assertIn("1.250 V", output)
        self.assertEqual(exit_code, 0)
        mock_detect.assert_called_once_with("USB0::1::2::3", "dmm")

    @patch("pyvisa.ResourceManager")
    def test_detect_model_from_resource_success(self, mock_rm_class) -> None:
        from testbench.cli.main import _detect_model_from_resource
        
        mock_rm = MagicMock()
        mock_instr = MagicMock()
        mock_instr.query.return_value = "KEITHLEY INSTRUMENTS INC.,MODEL 2110,8009937,02.02-02-01"
        mock_rm.open_resource.return_value = mock_instr
        mock_rm_class.return_value = mock_rm

        model = _detect_model_from_resource("USB0::1::2", "dmm")
        self.assertEqual(model, "keithley-2110")

    @patch("testbench.cli.main.discover_instruments")
    @patch("testbench.cli.main.bind_driver")
    def test_psu_measure_voltage(self, mock_bind, mock_discover) -> None:
        report = DiscoveryReport()
        identity = DeviceIdentity.parse("Siglent Technologies,SPD3303X-E,SERIAL,1.0,V1")
        device = DiscoveredDevice(identity=identity, resources=["TCPIP::192.0.2.10::INSTR"])
        report.devices[identity.key] = device
        mock_discover.return_value = report

        mock_driver = MagicMock()
        mock_driver.online.return_value = True
        mock_driver.get_voltage.return_value = 5.012
        mock_bind.return_value = mock_driver

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["psu", "measure", "voltage", "--channel", "1"])

        output = mock_stdout.getvalue()
        self.assertIn("5.012 V", output)
        self.assertEqual(exit_code, 0)
        mock_driver.get_voltage.assert_called_once_with(1)

    @patch("testbench.cli.main.discover_instruments")
    @patch("testbench.cli.main.bind_driver")
    def test_daq_list_modules(self, mock_bind, mock_discover) -> None:
        report = DiscoveryReport()
        identity = DeviceIdentity.parse("RIGOL TECHNOLOGIES,M300,SERIAL,01")
        device = DiscoveredDevice(identity=identity, resources=["TCPIP::192.0.2.20::INSTR"])
        report.devices[identity.key] = device
        mock_discover.return_value = report

        mock_driver = MagicMock()
        mock_driver.online.return_value = True
        
        module_mock = MagicMock()
        module_mock.manufacturer = "RIGOL"
        module_mock.model = "MC3132"
        module_mock.serial = "SERIAL_M"
        module_mock.firmware = "1.0"
        mock_driver.module_inventory.return_value = {200: module_mock}
        mock_bind.return_value = mock_driver

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["daq", "list-modules"])

        output = mock_stdout.getvalue()
        self.assertIn("200: RIGOL, MC3132, SERIAL_M, 1.0", output)
        self.assertEqual(exit_code, 0)

    @patch("testbench.cli.main.discover_instruments")
    @patch("testbench.cli.main.bind_driver")
    def test_daq_measure_voltage(self, mock_bind, mock_discover) -> None:
        report = DiscoveryReport()
        identity = DeviceIdentity.parse("RIGOL TECHNOLOGIES,M300,SERIAL,01")
        device = DiscoveredDevice(identity=identity, resources=["TCPIP::192.0.2.20::INSTR"])
        report.devices[identity.key] = device
        mock_discover.return_value = report

        mock_driver = MagicMock()
        mock_driver.online.return_value = True
        mock_driver.measure_voltage_dc.return_value = 0.050499
        mock_bind.return_value = mock_driver

        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            exit_code = main(["daq", "measure-voltage", "201"])

        output = mock_stdout.getvalue()
        self.assertIn("50.499 mV", output)
        self.assertEqual(exit_code, 0)
        mock_driver.measure_voltage_dc.assert_called_once_with(201)


if __name__ == "__main__":
    unittest.main()
