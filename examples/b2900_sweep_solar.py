from __future__ import annotations

import argparse
import csv
import sys
import time

from testbench.core.scpi import SCPISettings
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure and run a single solar sweep on the B2900."
    )
    parser.add_argument(
        "--resource",
        default="USB0::10893::37377::MY60440156::0::INSTR",
        help="VISA resource name for the instrument",
    )
    parser.add_argument(
        "--points",
        type=int,
        default=86,
        help="Number of sweep points (default: 86)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    smu = KeysightB2902B(resource_name=args.resource)
    npts = args.points

    smu.driver.write("*RST")
    smu.driver.write("*CLS")
    smu.driver.write(":INST:NSEL 1")

    # --- Source sweep setup ---
    smu.driver.write(":SOUR:FUNC VOLT")
    smu.driver.write(":SOUR:VOLT:MODE SWE")
    smu.driver.write(":SOUR:SWE:SPAC LIN")
    smu.driver.write(":SOUR:VOLT:STAR 0")
    smu.driver.write(":SOUR:VOLT:STOP 8.5")
    smu.driver.write(f":SOUR:VOLT:POIN {npts}")

    # --- Backdrive prevention (asymmetric protection) ---
    smu.driver.write(":SENS:CURR:PROT:POS 1e-9")  # or 1e-6 if needed
    smu.driver.write(":SENS:CURR:PROT:NEG 0.6")

    # --- Measurement config ---
    smu.driver.write(':SENS:FUNC "VOLT"')
    smu.driver.write(':SENS:FUNC "CURR"')
    smu.driver.write(":SENS:VOLT:NPLC 0.2")
    smu.driver.write(":SENS:CURR:NPLC 0.2")
    smu.driver.write("SENS1:WAIT:OFFS 0.005")

    # --- TRACE: must size buffer while FEED:CONT is NEV ---
    smu.driver.write(":TRAC:CLE")
    smu.driver.write(":TRAC:FEED:CONT NEV")
    smu.driver.write(f":TRAC:POIN {npts}")
    smu.driver.write(":TRAC:FEED SENS")
    smu.driver.write(":FORM:ELEM VOLT,CURR")
    smu.driver.write(":TRAC:FEED:CONT NEXT")

    # --- TRIGGER: make sure acquire/transient counts match sweep points ---
    smu.driver.write(f":TRIG:TRAN:COUN {npts}")
    smu.driver.write(f":TRIG:ACQ:COUN {npts}")
    smu.driver.write(":TRIG:SOUR IMM")

    # --- Run ---
    smu.driver.write(":OUTP ON")
    smu.driver.write(":INIT")
    smu.driver.query_raw("*OPC?")

    # --- Verify and fetch exactly what was stored ---
    act = int(float(smu.driver.query_raw(":TRAC:POIN:ACT?")))
    payload = smu.driver.query_raw(f":TRAC:DATA? 1,{act}")
    samples = _parse_trace_payload(payload)
    if not samples:
        print("No sweep data returned")
        return 1

    mpp = _find_maximum_power_point(samples)
    if mpp is None:
        print("Sweep completed but no positive power points were found.")
        return 0

    point, voltage, current, power = mpp
    print(
        f"Maximum power point: {power:.3f} W at "
        f"{voltage:.3f} V, {current:.3f} A (point {point}/{len(samples)})"
    )
    return 0


def _parse_trace_payload(payload: bytes) -> list[tuple[float, float]]:
    decoded = payload.decode("utf-8").strip()
    if not decoded:
        return []
    values = [float(part) for part in decoded.split(",") if part.strip()]
    if len(values) % 2 != 0:
        # Some firmware versions occasionally report an extra pending datum even
        # though the trace buffer only contains completed pairs. Drop the straggler
        # so downstream processing can continue.
        print(
            f"Warning: dropping last trace value from odd-length payload ({len(values)} entries)"
        )
        values = values[:-1]
    return [(values[i], values[i + 1]) for i in range(0, len(values), 2)]


def _find_maximum_power_point(
    samples: list[tuple[float, float]]
) -> tuple[int, float, float, float] | None:
    best_point: tuple[int, float, float, float] | None = None
    best_power = float("-inf")
    for idx, (voltage, current) in enumerate(samples, start=1):
        # The SMU sinks current from the panel, so delivered power is -V*I.
        power = -(voltage * current)
        if power <= 0:
            continue
        if power > best_power:
            best_power = power
            best_point = (idx, voltage, current, power)
    return best_point


if __name__ == "__main__":
    sys.exit(main())
