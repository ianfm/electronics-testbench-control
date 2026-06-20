from __future__ import annotations

import unittest

from testbench.core.formatters import format_engineering


class EngineeringFormatterTests(unittest.TestCase):
    def test_formatting_values(self) -> None:
        self.assertEqual(format_engineering(0.126246, "V"), "126.246 mV")
        self.assertEqual(format_engineering(4.88126e-05, "V"), "48.813 uV")
        self.assertEqual(format_engineering(16.28, "V"), "16.280 V")
        self.assertEqual(format_engineering(-0.11688, "V"), "-116.880 mV")
        self.assertEqual(format_engineering(0.32, "A"), "320.000 mA")
        self.assertEqual(format_engineering(0, "V"), "0.000 V")
        self.assertEqual(format_engineering(1250, "Hz"), "1.250 kHz")
        self.assertEqual(format_engineering(5.5e9, "Hz"), "5.500 GHz")

    def test_precision_argument(self) -> None:
        self.assertEqual(format_engineering(0.126246, "V", precision=1), "126.2 mV")
        self.assertEqual(format_engineering(0.126246, "V", precision=4), "126.2460 mV")


if __name__ == "__main__":
    unittest.main()
