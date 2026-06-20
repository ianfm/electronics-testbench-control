#!/usr/bin/env python3
"""Legacy M300/Keysight wire-harness ring-out workflow.

This preserves the untested script imported from the Erdos Miller example while
making it importable and configurable. Its routing sequence is not validated on
the local M300 hardware; use ``testbench.daq.rigol_m300`` for the manual-backed
DAQ driver.
"""

from __future__ import annotations

import argparse
import csv
import logging
import statistics
import sys
import time
from pathlib import Path

import pyvisa


WIRE_LIST = [
    "111", "112", "113", "114", "115", "116", "117", "118",
    "211", "212", "213", "214", "311", "312", "313", "314",
    "315", "316", "317", "318", "215", "216", "217", "218",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Legacy M300 ring-out test")
    parser.add_argument("--tool-serial", required=True)
    parser.add_argument("--master-values", type=Path, required=True)
    parser.add_argument("--m300-resource")
    parser.add_argument("--dmm-resource")
    parser.add_argument("--m300-serial", default="MM3A161750031")
    parser.add_argument("--dmm-serial", default="MY54507559")
    parser.add_argument("--logs", type=Path, default=Path("logs"))
    return parser.parse_args()


def load_matrix(path: Path) -> list[list[float]]:
    with path.open(newline="", encoding="utf-8") as source:
        return [[float(value) for value in row] for row in csv.reader(source)]


def find_resource(resources: tuple[str, ...], serial: str) -> str:
    matches = [resource for resource in resources if serial in resource]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one VISA resource containing {serial!r}, found {matches}"
        )
    return matches[0]


def make_measurement(m300, dmm, x: int, y: int, delay: float = 0.03) -> int:
    routes = f"{WIRE_LIST[y]},{WIRE_LIST[x]},401,410"
    m300.write(f"ROUT:CLOS (@{routes})")
    try:
        values = [
            float(value)
            for value in dmm.query("MEAS:FRES? AUTO").split(",")
            if value.strip()
        ]
        measurement = round(statistics.median(values))
    finally:
        m300.write(f"ROUT:OPEN (@{routes})")
    time.sleep(delay)
    if measurement >= 950_000:
        return 9_999_999
    if 0 <= measurement <= 20:
        return 0
    return int(measurement)


def run_ringout(m300, dmm, master: list[list[float]]) -> list[list[int]]:
    measured: list[list[int]] = []
    for y in range(12, 24):
        row = [make_measurement(m300, dmm, x, y) for x in range(12)]
        print(row)
        measured.append(row)

    for y in range(12):
        for x in range(12):
            reference = master[y][x]
            if not reference * 0.85 <= measured[y][x] <= reference * 1.15:
                reruns = [make_measurement(m300, dmm, x, y + 12) for _ in range(5)]
                measured[y][x] = round(statistics.median(reruns))
    return measured


def main() -> int:
    args = parse_args()
    args.logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=args.logs / "ErrorLog.txt",
        level=logging.WARNING,
    )
    output_path = args.logs / f"{args.tool_serial}_RO_Values_{time.strftime('%Y%m%d-%H%M')}.csv"
    master = load_matrix(args.master_values)

    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    m300_resource = args.m300_resource or find_resource(resources, args.m300_serial)
    dmm_resource = args.dmm_resource or find_resource(resources, args.dmm_serial)
    m300 = rm.open_resource(m300_resource, write_termination="\n", timeout=5000)
    dmm = rm.open_resource(dmm_resource, write_termination="\n", timeout=5000)
    try:
        print(m300.query("*IDN?").strip())
        print(dmm.query("*IDN?").strip())
        measured = run_ringout(m300, dmm, master)
        with output_path.open("w", newline="", encoding="utf-8") as destination:
            csv.writer(destination).writerows(measured)
        print(f"Wrote {output_path}")
        return 0
    except Exception:
        logging.exception("Ring-out test failed")
        raise
    finally:
        m300.close()
        dmm.close()
        rm.close()


if __name__ == "__main__":
    sys.exit(main())
