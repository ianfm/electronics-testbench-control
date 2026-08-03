from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

DEFAULT_RESOURCE = "USB0::10893::37377::MY60440156::0::INSTR"

DEFAULT_CH1_VOLTAGE = 6.0
DEFAULT_CH1_LIMIT = 0.25
DEFAULT_CH2_VOLTAGE = 3.2
DEFAULT_CH2_LIMIT = -2.0
DEFAULT_LIMIT_START = 0.0
DEFAULT_LIMIT_STOP = 0.3
DEFAULT_LIMIT_STEPS = 31
DEFAULT_SINK_START = 2.5
DEFAULT_SINK_STOP = 3.6
DEFAULT_SINK_STEPS = 21
DEFAULT_SETTLE_S = 2.0
DEFAULT_TIMEOUT_MS = 30000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MPPT profile sequence with spot and sweep scenarios."
    )
    parser.add_argument(
        "--resource",
        default=DEFAULT_RESOURCE,
        help=f"VISA resource string for the SMU (default: {DEFAULT_RESOURCE})",
    )
    parser.add_argument(
        "--description",
        help=(
            "Free-form label that becomes part of the CSV file name "
            "(default: current date/time stamp)"
        ),
    )
    parser.add_argument(
        "--limit-start",
        type=float,
        default=DEFAULT_LIMIT_START,
        help="Starting current limit for the channel 1 sweep (default: 0 A)",
    )
    parser.add_argument(
        "--limit-stop",
        type=float,
        default=DEFAULT_LIMIT_STOP,
        help="Ending current limit for the channel 1 sweep (default: 0.3 A)",
    )
    parser.add_argument(
        "--limit-steps",
        type=int,
        default=DEFAULT_LIMIT_STEPS,
        help="Number of steps for the channel 1 compliance sweep (default: 31)",
    )
    parser.add_argument(
        "--sink-start",
        type=float,
        default=DEFAULT_SINK_START,
        help="Starting channel 2 sink voltage for the sweeps (default: 2.5 V)",
    )
    parser.add_argument(
        "--sink-stop",
        type=float,
        default=DEFAULT_SINK_STOP,
        help="Ending channel 2 sink voltage for the sweeps (default: 3.6 V)",
    )
    parser.add_argument(
        "--sink-steps",
        type=int,
        default=DEFAULT_SINK_STEPS,
        help="Number of points for the channel 2 sink sweeps (default: 21)",
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=DEFAULT_SETTLE_S,
        help="Seconds to wait after each change before sampling (default: 0.2)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"Override VISA/SCPI timeout in milliseconds (default: {DEFAULT_TIMEOUT_MS})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    description = sanitize_description(args.description)
    csv_path = Path("data") / f"mppt_profile_{description}.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_file = csv_path.open("w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow(
        [
            "scenario",
            "step",
            "timestamp",
            "channel",
            "ch1_set_volts",
            "ch1_limit_a",
            "ch2_set_volts",
            "ch2_limit_a",
            "ch1_measured_volts",
            "ch1_measured_amps",
            "ch2_measured_volts",
            "ch2_measured_amps",
        ]
    )
    csv_file.flush()

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        csv_file.close()
        return 1

    driver = smu.driver
    driver.write("*RST")
    driver.write("*CLS")

    ch1_voltage = DEFAULT_CH1_VOLTAGE
    ch1_limit = DEFAULT_CH1_LIMIT
    ch2_voltage = DEFAULT_CH2_VOLTAGE
    ch2_limit = DEFAULT_CH2_LIMIT

    # Channel 1 setup
    driver.write(":SOUR1:FUNC VOLT")
    driver.write(":SOUR1:VOLT:MODE FIX")
    driver.write(f":SOUR1:VOLT:LEV {ch1_voltage}")
    driver.write(':SENS1:FUNC "VOLT"')
    driver.write(':SENS1:FUNC "CURR"')
    driver.write(":SENS1:VOLT:RANG:AUTO ON")
    driver.write(":SENS1:CURR:RANG:AUTO ON")
    driver.write(":SENS1:VOLT:NPLC 0.2")
    driver.write(":SENS1:CURR:NPLC 0.2")
    driver.write("SENS1:WAIT:OFFS 0.005")
    driver.write(f":SENS1:CURR:PROT {abs(ch1_limit)}")

    # Channel 2 setup
    driver.write(":SOUR2:FUNC VOLT")
    driver.write(":SOUR2:VOLT:MODE FIX")
    driver.write(f":SOUR2:VOLT:LEV {ch2_voltage}")
    driver.write(':SENS2:FUNC "VOLT"')
    driver.write(':SENS2:FUNC "CURR"')
    driver.write(":SENS2:VOLT:RANG:AUTO ON")
    driver.write(":SENS2:CURR:RANG:AUTO ON")
    driver.write(":SENS2:VOLT:NPLC 0.2")
    driver.write(":SENS2:CURR:NPLC 0.2")
    driver.write("SENS2:WAIT:OFFS 0.005")
    driver.write(f":SENS2:CURR:PROT {abs(ch2_limit)}")

    # Enable outputs
    driver.write(":OUTP1 ON")
    driver.write(":OUTP2 ON")

    try:
        step = 0
        for limit in linear_space(args.limit_start, args.limit_stop, args.limit_steps):
            step += 1
            ch1_limit = limit
            driver.write(f":SENS1:CURR:PROT {abs(ch1_limit)}")
            record_measurements(
                driver,
                writer,
                csv_file,
                "ch1_limit_sweep",
                step,
                ch1_voltage,
                ch1_limit,
                ch2_voltage,
                ch2_limit,
                args.settle,
            )

        ch1_limit = DEFAULT_CH1_LIMIT
        driver.write(f":SENS1:CURR:PROT {abs(ch1_limit)}")

        step = 0
        for voltage in linear_space(args.sink_start, args.sink_stop, args.sink_steps):
            step += 1
            ch2_voltage = voltage
            driver.write(f":SOUR2:VOLT:LEV {ch2_voltage}")
            record_measurements(
                driver,
                writer,
                csv_file,
                "ch2_voltage_sweep_default_limit",
                step,
                ch1_voltage,
                ch1_limit,
                ch2_voltage,
                ch2_limit,
                args.settle,
            )

        ch1_limit = 0.125
        driver.write(f":SENS1:CURR:PROT {abs(ch1_limit)}")

        step = 0
        for voltage in linear_space(args.sink_start, args.sink_stop, args.sink_steps):
            step += 1
            ch2_voltage = voltage
            driver.write(f":SOUR2:VOLT:LEV {ch2_voltage}")
            record_measurements(
                driver,
                writer,
                csv_file,
                "ch2_voltage_sweep_125ma_limit",
                step,
                ch1_voltage,
                ch1_limit,
                ch2_voltage,
                ch2_limit,
                args.settle,
            )
    finally:
        try:
            driver.write(":OUTP1 OFF")
            driver.write(":OUTP2 OFF")
        finally:
            csv_file.close()

    print(f"MPPT profile written to {csv_path}")
    return 0


def record_measurements(
    driver,
    writer,
    csv_file,
    scenario: str,
    step: int,
    ch1_voltage: float,
    ch1_limit: float,
    ch2_voltage: float,
    ch2_limit: float,
    settle: float,
) -> None:
    time.sleep(max(0.0, settle))
    timestamp = datetime.now().isoformat(timespec="milliseconds")
    v1 = float(driver.query("MEAS:VOLT? (@1)"))
    i1 = float(driver.query("MEAS:CURR? (@1)"))
    v2 = float(driver.query("MEAS:VOLT? (@2)"))
    i2 = float(driver.query("MEAS:CURR? (@2)"))
    writer.writerow(
        [
            scenario,
            step,
            timestamp,
            1,
            ch1_voltage,
            ch1_limit,
            ch2_voltage,
            ch2_limit,
            v1,
            i1,
            v2,
            i2,
        ]
    )
    writer.writerow(
        [
            scenario,
            step,
            timestamp,
            2,
            ch1_voltage,
            ch1_limit,
            ch2_voltage,
            ch2_limit,
            v1,
            i1,
            v2,
            i2,
        ]
    )
    csv_file.flush()


def linear_space(start: float, stop: float, steps: int):
    if steps <= 1:
        yield stop
        return
    delta = (stop - start) / float(steps - 1)
    for idx in range(steps):
        yield start + idx * delta


def sanitize_description(description: str | None) -> str:
    if not description:
        description = time.strftime("%Y%m%d_%H%M%S")
    cleaned = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_" for char in description
    ).strip("_")
    return cleaned or "session"


if __name__ == "__main__":
    sys.exit(main())
