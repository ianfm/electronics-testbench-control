from __future__ import annotations

import argparse
import time

from testbench.binding import bind_driver, matching_device
from testbench.core.formatters import format_engineering
from testbench.daq.rigol_m300 import RigolM300
from testbench.discovery import discover_instruments


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure a multi-channel scan list and measure repeatedly on the Rigol M300"
    )
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource name (e.g., TCPIP::192.168.86.170::INSTR)",
    )
    parser.add_argument(
        "--scans",
        type=int,
        default=5,
        help="Number of scans to perform (default: 5)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Interval between scans in seconds (default: 1.0)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 1. Connect to the DAQ system
    if args.resource:
        daq = RigolM300(resource_name=args.resource)
    else:
        # Try default lookup first
        daq = RigolM300()
        if not daq.online():
            print("Direct VISA lookup did not find the M300. Running discovery...")
            report = discover_instruments()
            device = matching_device("rigol-m300", report)
            if device is not None:
                # Release the initial unconnected instance's resource manager
                try:
                    daq.driver.rm.close()
                except Exception:
                    pass
                daq = bind_driver("rigol-m300", device)

    if not daq.online():
        print("Rigol M300 DAQ system not found.")
        return

    print("Connected to:", daq.identify().strip())

    # 2. Define the 16 channels (slot 200, channels 201 to 216)
    channels = list(range(201, 217))
    print(f"Setting up scan list for channels: {channels[0]} to {channels[-1]}")

    try:
        # 3. Configure the scan list on the hardware
        daq.setup_scan(channels)

        # 4. Perform the scans repeatedly
        for scan_idx in range(1, args.scans + 1):
            print(f"\n--- Scan {scan_idx} of {args.scans} ---")
            start_time = time.time()
            readings = daq.read_scan()
            elapsed = time.time() - start_time

            # Print results formatted
            for i, ch in enumerate(channels):
                val = readings[i]
                print(f"  Channel {ch}: {format_engineering(val, 'V')}")

            print(f"Scan completed in {elapsed:.3f} seconds")

            if scan_idx < args.scans:
                time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nScan sequence interrupted by user.")
    finally:
        # 5. Crucial: Clean up and open the multiplexer relays for safety
        print("\nClearing scan list (safely opening all relays)...")
        daq.clear_scan()
        print("Done.")


if __name__ == "__main__":
    main()
