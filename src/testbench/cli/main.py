from __future__ import annotations

import argparse
import sys

from testbench.dmm.keithley_2110 import Keithley2110
from testbench.dmm.rigol_dm3058e import RigolDM3058E
from testbench.psu.siglent_spd1305x import SiglentSPD1305X
from testbench.psu.siglent_spd3303x import SiglentSPD3303X


DMM_MODELS = {
    "rigol-dm3058e": RigolDM3058E,
    "keithley-2110": Keithley2110,
}

PSU_MODELS = {
    "siglent-spd1305x": SiglentSPD1305X,
    "siglent-spd3303x": SiglentSPD3303X,
}


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Testbench instrument helper CLI")
    sub = parser.add_subparsers(dest="domain", required=True)

    dmm = sub.add_parser("dmm", help="Digital multimeter actions")
    dmm.add_argument("--model", choices=DMM_MODELS.keys(), required=True)
    dmm.add_argument("--measure", choices=["voltage", "current", "resistance"])

    psu = sub.add_parser("psu", help="Power supply actions")
    psu.add_argument("--model", choices=PSU_MODELS.keys(), required=True)
    psu.add_argument("--channel", type=int, default=1)
    psu.add_argument("--set-voltage", type=float)
    psu.add_argument("--set-current", type=float)
    psu.add_argument("--power", choices=["on", "off"])

    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)

    if args.domain == "dmm":
        cls = DMM_MODELS[args.model]
        dmm = cls()
        if not dmm.online():
            print("DMM not found")
            return 1
        if args.measure == "voltage":
            print(dmm.measure_voltage_dc())
        elif args.measure == "current":
            print(dmm.measure_current_dc())
        elif args.measure == "resistance":
            print(dmm.measure_resistance())
        else:
            print("No measurement requested")
        return 0

    if args.domain == "psu":
        cls = PSU_MODELS[args.model]
        psu = cls()
        if not psu.online():
            print("PSU not found")
            return 1
        ch = args.channel
        if args.set_voltage is not None:
            psu.set_voltage(ch, args.set_voltage)
        if args.set_current is not None:
            psu.set_current(ch, args.set_current)
        if args.power == "on":
            psu.output_on(ch)
        elif args.power == "off":
            psu.output_off(ch)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
