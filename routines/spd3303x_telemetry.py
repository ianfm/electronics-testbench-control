from __future__ import annotations

import argparse

from testbench.binding import bind_driver, matching_device
from testbench.core.formatters import format_engineering
from testbench.discovery import discover_instruments


def main() -> None:
    print("Running discovery to locate Siglent SPD3303X PSU...")
    report = discover_instruments()

    device = matching_device("siglent-spd3303x", report)
    if device is None:
        print("Siglent SPD3303X PSU not found.")
        return

    print(f"Found SPD3303X on resource {device.resources[0]}")
    psu = bind_driver("siglent-spd3303x", device)

    try:
        print("Connected to:", psu.identify().strip())

        # Query and format both channels
        for channel in (1, 2):
            v = psu.get_voltage(channel)
            i = psu.get_current(channel)

            v_str = format_engineering(v, "V") if v is not None else "N/A"
            i_str = format_engineering(i, "A") if i is not None else "N/A"

            print(f"\nChannel {channel} Telemetry:")
            print(f"  Voltage: {v_str}")
            print(f"  Current: {i_str}")
    finally:
        try:
            psu.driver.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
