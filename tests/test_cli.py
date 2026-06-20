from __future__ import annotations

import io
import unittest
from unittest.mock import patch, MagicMock
import argparse

from testbench.cli.main import main
from testbench.discovery import DiscoveryReport, DiscoveryAttempt


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


if __name__ == "__main__":
    unittest.main()
