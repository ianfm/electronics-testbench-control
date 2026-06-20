from __future__ import annotations

import argparse

from testbench.binding import bind_driver, matching_device
from testbench.core.formatters import format_engineering
from testbench.discovery import discover_instruments
from testbench.dmm.keithley_2110 import Keithley2110
from testbench.dmm.rigol_dm3058e import RigolDM3058E


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query connected DMMs and format readings in engineering units"
    )
    parser.add_argument(
        "--measure",
        choices=["voltage", "current"],
        default="voltage",
        help="Measurement type (default: voltage)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("Running discovery to locate DMMs...")
    report = discover_instruments()

    dmm_models = {
        "keithley-2110": Keithley2110,
        "rigol-dm3058e": RigolDM3058E,
    }

    found_dmms = []

    for name, cls in dmm_models.items():
        device = matching_device(name, report)
        if device is not None:
            print(f"Found {name} on resource {device.resources[0]}")
            dmm = bind_driver(name, device)
            found_dmms.append((name, dmm))

    if not found_dmms:
        print("No Keithley 2110 or Rigol DM3058E DMMs identified.")
        return

    print("\n--- Running DMM Measurements ---")
    for name, dmm in found_dmms:
        try:
            if args.measure == "voltage":
                val = dmm.measure_voltage_dc()
                unit = "V"
            else:
                val = dmm.measure_current_dc()
                unit = "A"

            formatted = format_engineering(val, unit)
            print(f"{name.upper()}: {formatted}")
        except Exception as e:
            print(f"Failed to read from {name}: {e}")
        finally:
            # Clean up the resource connection
            try:
                dmm.driver.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
