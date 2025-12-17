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
    smu.driver.query_raw(f":TRAC:DATA? 1,{act}")  # V1,I1,V2,I2,...
    return 0


if __name__ == "__main__":
    sys.exit(main())
