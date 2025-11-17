"""
Example: sample DC current repeatedly with a DMM and report max/avg.

Uses the packaged drivers so you can swap in another DMM by changing the import.
"""

import argparse
import signal
import sys
import time
from typing import Optional

# from testbench.dmm.keithley_2110 import Keithley2110 as DMM
from testbench.dmm.rigol_dm3058e import RigolDM3058E as DMM


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure DC current repeatedly")
    parser.add_argument(
        "--num-samples",
        type=int,
        help="Stop after this many samples (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Stop after this many seconds (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.2,
        help="Seconds to wait between samples (default: 0.2)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    dmm = DMM()
    if not dmm.online():
        print("DMM not found")
        return 1

    max_current = float("-inf")
    avg_current = 0.0
    sample_count = 0
    start_time = time.time()
    duration: Optional[float] = args.duration

    def handle_sigint(sig, frame):
        print("\nStopping (Ctrl+C)")
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_sigint)

    try:
        while True:
            reading = dmm.measure_current_dc()
            sample_count += 1
            max_current = max(max_current, reading)
            avg_current += (reading - avg_current) / sample_count

            print(f"{sample_count}: {reading:.6f} A")

            if args.num_samples and sample_count >= args.num_samples:
                break

            if duration and (time.time() - start_time) >= duration:
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    print("\nSummary")
    print(f"Samples: {sample_count}")
    if sample_count:
        print(f"Max: {max_current:.6f} A")
        print(f"Avg: {avg_current:.6f} A")
    return 0


if __name__ == "__main__":
    sys.exit(main())
