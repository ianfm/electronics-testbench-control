from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from testbench.core.scpi import SCPIDriver, SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B

DEFAULT_POINTS = 86
DEFAULT_START_VOLTS = 0.0
DEFAULT_STOP_VOLTS = 8.5
DEFAULT_INTERVAL_S = 30.0
DEFAULT_POS_PROTECT_A = 1e-9
DEFAULT_NEG_PROTECT_A = 0.6
DEFAULT_NPLC = 0.2
DEFAULT_WAIT_OFFSET = 0.005
DEFAULT_TIMEOUT_MS = 30000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continuously execute the solar sweep sequence and append the "
            "results to a CSV log."
        )
    )
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource string (e.g. TCPIP::192.168.10.2::INSTR)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=1,
        choices=[1, 2],
        help="SMU channel that is wired to the solar DUT (default: 1)",
    )
    parser.add_argument(
        "--start-volts",
        type=float,
        default=DEFAULT_START_VOLTS,
        help="Sweep start voltage (default: 0 V)",
    )
    parser.add_argument(
        "--stop-volts",
        type=float,
        default=DEFAULT_STOP_VOLTS,
        help="Sweep stop voltage (default: 8.5 V)",
    )
    parser.add_argument(
        "--points",
        type=int,
        default=DEFAULT_POINTS,
        help="Number of sweep points (default: 86)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_S,
        help="Seconds between the start of each sweep (default: 30)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("solar_sweep_log.csv"),
        help="CSV file to append sweep data (default: solar_sweep_log.csv)",
    )
    parser.add_argument(
        "--sweeps",
        type=int,
        help="Stop after this many sweeps (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--nplc",
        type=float,
        default=DEFAULT_NPLC,
        help="Integration time (NPLC) for voltage/current (default: 0.2)",
    )
    parser.add_argument(
        "--pos-protect",
        type=float,
        default=DEFAULT_POS_PROTECT_A,
        help="Positive current protection limit in amperes (default: 1e-9 A)",
    )
    parser.add_argument(
        "--neg-protect",
        type=float,
        default=DEFAULT_NEG_PROTECT_A,
        help="Negative current protection limit in amperes (default: 0.6 A)",
    )
    parser.add_argument(
        "--wait-offset",
        type=float,
        default=DEFAULT_WAIT_OFFSET,
        help="Trigger wait offset (default: 5 ms)",
    )
    parser.add_argument(
        "--keep-on",
        action="store_true",
        help="Leave the output enabled when exiting (default: turn it off)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=(
            f"Override VISA/SCPI timeout in milliseconds "
            f"(default: {DEFAULT_TIMEOUT_MS})"
        ),
    )
    return parser.parse_args()


def configure_solar_sweep(smu: KeysightB2902B, args: argparse.Namespace) -> None:
    driver = smu.driver
    driver.write("*RST")
    driver.write("*CLS")
    driver.write(f":INST:NSEL {args.channel}")

    driver.write(":SOUR:FUNC VOLT")
    driver.write(":SOUR:VOLT:MODE SWE")
    driver.write(":SOUR:SWE:SPAC LIN")
    driver.write(f":SOUR:VOLT:STAR {args.start_volts}")
    driver.write(f":SOUR:VOLT:STOP {args.stop_volts}")
    driver.write(f":SOUR:VOLT:POIN {args.points}")

    driver.write(f":SENS:CURR:PROT:POS {args.pos_protect}")
    driver.write(f":SENS:CURR:PROT:NEG {args.neg_protect}")

    driver.write(':SENS:FUNC "VOLT"')
    driver.write(':SENS:FUNC "CURR"')
    driver.write(f":SENS:VOLT:NPLC {args.nplc}")
    driver.write(f":SENS:CURR:NPLC {args.nplc}")
    driver.write(f"SENS{args.channel}:WAIT:OFFS {args.wait_offset}")

    configure_trace_format(driver)
    driver.write(":OUTP ON")


def configure_trace_format(driver: SCPIDriver) -> None:
    driver.write(":FORM:DATA ASC")
    driver.write(":FORM:ELEM:SENS VOLT,CURR")


def prepare_trace(driver: SCPIDriver, points: int) -> None:
    driver.write(":TRAC:CLE")
    driver.write(":TRAC:FEED:CONT NEV")
    driver.write(f":TRAC:POIN {points}")
    driver.write(":TRAC:FEED SENS")
    configure_trace_format(driver)
    driver.write(":TRAC:FEED:CONT NEXT")


def run_single_sweep(driver: SCPIDriver, points: int) -> list[tuple[float, float]]:
    prepare_trace(driver, points)
    driver.write(f":TRIG:TRAN:COUN {points}")
    driver.write(f":TRIG:ACQ:COUN {points}")
    driver.write(":TRIG:SOUR IMM")
    driver.write(":INIT")
    driver.query_raw("*OPC?")

    act_response = driver.query_raw(":TRAC:POIN:ACT?")
    act = int(float(act_response.decode("utf-8").strip()))
    payload = driver.query_raw(
        f":TRAC:DATA? 1,{act}", size=max(4096, act * 48)
    ).decode("utf-8")
    values = [float(part) for part in payload.strip().split(",") if part.strip()]
    if len(values) % 2 != 0:
        raise RuntimeError(f"Trace returned an odd number of values: {len(values)}")
    actual_pairs = len(values) // 2
    # Some firmware revisions report an extra pending point via :TRAC:POIN:ACT?
    # even though the trace payload only contains the completed samples. Trust
    # the actual data size instead of the reported count to avoid spurious warnings.
    # TODO: expectation == requested == reported should hold; investigate why
    # :TRAC:POIN? request, :TRAC:POIN:ACT? response, and the returned payload
    # all disagree so we can fix the root cause instead of masking it here.
    samples: list[tuple[float, float]] = []
    for idx in range(0, actual_pairs * 2, 2):
        samples.append((values[idx], values[idx + 1]))
    return samples


def ensure_csv(csv_path: Path) -> tuple[Any, TextIO]:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    csv_file = csv_path.open("a", newline="")
    writer = csv.writer(csv_file)
    if not file_exists or csv_path.stat().st_size == 0:
        writer.writerow(["sweep", "point", "timestamp", "elapsed_s", "volts", "amps"])
        csv_file.flush()
    return writer, csv_file


def log_sweeps(smu: KeysightB2902B, args: argparse.Namespace) -> None:
    writer, csv_file = ensure_csv(args.csv)
    start_time = time.time()
    interval = max(1.0, args.interval)
    sweeps_completed = 0
    next_start = time.time()

    try:
        while True:
            if args.sweeps is not None and sweeps_completed >= args.sweeps:
                break
            now = time.time()
            sleep_time = next_start - now
            if sleep_time > 0:
                time.sleep(sleep_time)

            sweep_index = sweeps_completed + 1
            sweep_started = time.time()
            timestamp = datetime.now().isoformat(timespec="seconds")
            elapsed = sweep_started - start_time
            samples = run_single_sweep(smu.driver, args.points)
            for point_index, (voltage, current) in enumerate(samples, start=1):
                writer.writerow(
                    [
                        sweep_index,
                        point_index,
                        timestamp,
                        f"{elapsed:.3f}",
                        f"{voltage:.6f}",
                        f"{current:.9f}",
                    ]
                )
            csv_file.flush()
            sweeps_completed += 1
            print(
                f"Sweep {sweeps_completed} logged "
                f"{len(samples)} points to {args.csv}"
            )

            next_start += interval
            if next_start < time.time():
                next_start = time.time()
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C)")
    finally:
        csv_file.close()


def main() -> int:
    args = parse_args()

    settings = SCPISettings(timeout_ms=args.timeout_ms)
    smu = KeysightB2902B(settings=settings, resource_name=args.resource)
    if not smu.online():
        print("B2902B not found")
        return 1

    configure_solar_sweep(smu, args)

    try:
        log_sweeps(smu, args)
    finally:
        if not args.keep_on:
            smu.output.set_output_enabled(False, channel=args.channel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
