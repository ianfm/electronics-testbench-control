from __future__ import annotations

import argparse
import csv
import sys
import time

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B


SAMPLE_INTERVAL = 0.5  # default 2 samples per second


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream voltage/current readings from a B2900 channel."
    )
    parser.add_argument("--channel", type=int, default=2, help="SMU channel (1 or 2)")
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource string (e.g. TCPIP::192.168.10.2::INSTR)",
    )
    parser.add_argument("--volts", type=float, required=True, help="Voltage setpoint")
    parser.add_argument(
        "--compliance-ma",
        type=float,
        default=50.0,
        help="Current compliance in milliamps (default: 50 mA)",
    )
    parser.add_argument(
        "--nplc",
        type=float,
        default=1.0,
        help="Number of power line cycles for measurement integration",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Seconds to wait after enabling the output before the first reading",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=SAMPLE_INTERVAL,
        help="Seconds between samples (default: 0.5s for 2 samples/sec)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        help="Seconds to stream before stopping (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--remote-sense",
        action="store_true",
        help="Enable 4-wire remote sensing for voltage/current measurements",
    )
    parser.add_argument(
        "--keep-on",
        action="store_true",
        help="Leave the output enabled when exiting (default: turn it off)",
    )
    parser.add_argument(
        "--csv",
        type=str,
        help="Optional path to log the readings as CSV (sample,time,V,I)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_file = open(args.csv, "w", newline="") if args.csv else None
    csv_writer = csv.writer(csv_file) if csv_file else None
    if csv_writer:
        csv_writer.writerow(["sample", "time_s", "V", "I"])

    settings = SCPISettings()
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        return 1

    ch = args.channel
    smu.source.set_function_mode("VOLT", channel=ch)
    smu.source.set_voltage_mode("FIX", channel=ch)
    smu.source.set_voltage_level(args.volts, channel=ch)

    smu.sense.set_functions(["VOLT", "CURR"], channel=ch)
    if args.remote_sense:
        smu.sense.enable_remote_sense(True, channel=ch)
    smu.sense.set_voltage_auto_range_enabled(True, channel=ch)
    smu.sense.set_current_nplc(args.nplc, channel=ch)
    smu.sense.set_current_auto_range_enabled(True, channel=ch)
    smu.sense.set_current_compliance(args.compliance_ma / 1000.0, channel=ch)

    smu.output.set_output_enabled(True, channel=ch)
    time.sleep(args.delay)

    interval = max(0.01, args.interval)
    start = time.time()
    next_sample = start
    target_time = start + args.duration if args.duration else None
    count = 0

    try:
        while True:
            voltage = float(smu.driver.query(f"MEAS:VOLT? (@{ch})"))
            current = float(smu.driver.query(f"MEAS:CURR? (@{ch})"))
            timestamp = time.time() - start
            count += 1
            print(f"{count:04d} | {timestamp:8.3f}s | V={voltage:.6f} V | I={current:.6f} A")
            if csv_writer:
                csv_writer.writerow(
                    [count, f"{timestamp:.3f}", f"{voltage:.6f}", f"{current:.6f}"]
                )
                csv_file.flush()
            if target_time and time.time() >= target_time:
                break
            next_sample += interval
            sleep_time = max(0.0, next_sample - time.time())
            time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C)")
    finally:
        if not args.keep_on:
            smu.output.set_output_enabled(False, channel=ch)
        if csv_file:
            csv_file.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
