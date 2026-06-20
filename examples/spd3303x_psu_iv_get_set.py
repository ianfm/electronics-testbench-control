from __future__ import annotations

import argparse
import time

from testbench.binding import bind_driver, matching_device
from testbench.core.scpi import SCPISettings
from testbench.discovery import discover_instruments
from testbench.psu.siglent_spd3303x import SiglentSPD3303X


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set and measure voltage/current on a Siglent SPD3303X PSU"
    )
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource name (e.g. TCPIP::192.168.86.169::INSTR)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=1,
        help="PSU channel to configure and read back (default: 1)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.resource:
        psu = SiglentSPD3303X(resource_name=args.resource)
    else:
        # Try default lookup first
        psu = SiglentSPD3303X()
        if not psu.online():
            print("Direct VISA lookup did not find the PSU. Running network/VISA discovery...")
            report = discover_instruments()
            device = matching_device("siglent-spd3303x", report)
            if device is not None:
                # Release the initial unconnected instance's resource manager
                try:
                    psu.driver.rm.close()
                except Exception:
                    pass
                psu = bind_driver("siglent-spd3303x", device)

    if not psu.online():
        print("SPD3303X PSU not found. Make sure it is connected and reachable.")
        return

    print("Connected to:", psu.identify().strip())

    # Set and readback values
    channel = args.channel
    target_volts = 1.5
    target_amps = 0.1

    print(f"\nConfiguring Channel {channel}:")
    print(f"Setting voltage to {target_volts} V")
    psu.set_voltage(channel, target_volts)

    print(f"Setting current to {target_amps} A")
    psu.set_current(channel, target_amps)

    time.sleep(1.0)

    # Readback
    measured_volts = psu.get_voltage(channel)
    measured_amps = psu.get_current(channel)
    print(f"\nChannel {channel} Readback:")
    print(f"Voltage: {measured_volts} V")
    print(f"Current: {measured_amps} A")


if __name__ == "__main__":
    main()
