from __future__ import annotations

import argparse
import sys
import time
from typing import Sequence

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug READ? parsing by showing raw responses and element lists."
    )
    parser.add_argument("--channel", type=int, default=1, help="SMU channel (1 or 2)")
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
        "--count",
        type=int,
        default=5,
        help="Number of READ? commands to issue (default: 5)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Delay between successive READ? commands",
    )
    parser.add_argument(
        "--format",
        default="ASC",
        help="Value for :FORM:DATA (default: ASC)",
    )
    parser.add_argument(
        "--elements",
        default="VOLT,CURR",
        help="Comma-separated :FORM:ELEM:SENS list (default: VOLT,CURR)",
    )
    parser.add_argument(
        "--remote-sense",
        action="store_true",
        help="Enable remote sense (4-wire) before sampling",
    )
    parser.add_argument(
        "--keep-on",
        action="store_true",
        help="Leave the output enabled on exit (default: turn it off)",
    )
    return parser.parse_args()


def tokens_to_functions(tokens: Sequence[str]) -> list[str]:
    functions: list[str] = []
    mapping = {"VOLT": "VOLT", "CURR": "CURR", "RES": "RES"}
    for token in tokens:
        func = mapping.get(token)
        if func and func not in functions:
            functions.append(func)
    if not functions:
        functions.append("CURR")
    return functions


def main() -> int:
    args = parse_args()
    elements = [token.strip().upper() for token in args.elements.split(",") if token.strip()]
    if not elements:
        print("No valid elements specified")
        return 1

    settings = SCPISettings()
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        return 1

    ch = args.channel
    smu.source.set_function_mode("VOLT", channel=ch)
    smu.source.set_voltage_mode("FIX", channel=ch)
    smu.source.set_voltage_level(args.volts, channel=ch)

    sense_functions = tokens_to_functions(elements)
    smu.sense.set_functions(sense_functions, channel=ch)
    if "VOLT" in sense_functions:
        smu.sense.set_voltage_auto_range_enabled(True, channel=ch)
    if "CURR" in sense_functions:
        smu.sense.set_current_auto_range_enabled(True, channel=ch)
        smu.sense.set_current_compliance(args.compliance_ma / 1000.0, channel=ch)
        smu.sense.set_current_nplc(args.nplc, channel=ch)
    if args.remote_sense:
        smu.sense.enable_remote_sense(True, channel=ch)

    smu.driver.write(f":FORM:DATA {args.format.strip().upper()}")
    smu.driver.write(f":FORM:ELEM:SENS {','.join(elements)}")

    smu.output.set_output_enabled(True, channel=ch)
    time.sleep(args.delay)

    channel_clause = f"(@{ch})"

    try:
        for idx in range(1, args.count + 1):
            raw = smu.driver.query(f"READ? {channel_clause}")
            parts = [part.strip() for part in raw.split(",")]
            print(f"\nREAD #{idx}")
            print(f"Raw response: {raw}")
            for i, value in enumerate(parts):
                print(f"  [{i:02d}] {value}")
            curr = smu.driver.query(f"MEAS:CURR? {channel_clause}")
            volt = smu.driver.query(f"MEAS:VOLT? {channel_clause}")
            print(f"MEAS:VOLT? -> {volt}")
            print(f"MEAS:CURR? -> {curr}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C)")
    finally:
        if not args.keep_on:
            smu.output.set_output_enabled(False, channel=ch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

