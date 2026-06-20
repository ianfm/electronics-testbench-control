from __future__ import annotations

import argparse
import sys
from pathlib import Path

from testbench.discovery import DiscoveryConfig, discover_instruments
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


def _add_discovery_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--subnet",
        action="append",
        default=[],
        help="IPv4 subnet to scan; repeat as needed (default: active local subnets)",
    )
    parser.add_argument(
        "--no-network-scan",
        action="store_true",
        help="Use cached and VISA-enumerated resources only",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=0.25,
        help="TCP port-probe timeout in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--idn-timeout-ms",
        type=int,
        default=2000,
        help="Timeout for each *IDN? attempt (default: 2000)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=64,
        help="Concurrent TCP probes (default: 64)",
    )
    parser.add_argument(
        "--max-hosts",
        type=int,
        default=1024,
        help="Maximum hosts allowed in each automatically scanned subnet",
    )
    parser.add_argument(
        "--raw-scpi-port",
        action="append",
        type=int,
        default=[],
        help="Raw SCPI TCP port; repeat as needed (default: 5025)",
    )
    parser.add_argument(
        "--filter",
        default="",
        help="Optional VISA resource filter",
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Probe USB RAW resources returned by VISA",
    )
    parser.add_argument(
        "--include-serial",
        action="store_true",
        help="Probe serial resources returned by VISA",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Do not read or update the successful-resource cache",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="Override the discovery cache path",
    )
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help="Print resources and network endpoints that failed identification",
    )
    # Compatibility with the former examples/list_devices.py interface.
    parser.add_argument("--idn", action="store_true", help=argparse.SUPPRESS)


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

    discover = sub.add_parser(
        "discover", help="Identify instruments using VISA and LAN fallbacks"
    )
    _add_discovery_arguments(discover)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)

    if args.domain == "discover":
        return _run_discovery(args)

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


def _run_discovery(args: argparse.Namespace) -> int:
    config_kwargs = {
        "visa_filter": args.filter,
        "include_raw_usb": args.include_raw,
        "include_serial": args.include_serial,
        "scan_network": not args.no_network_scan,
        "subnets": tuple(args.subnet),
        "raw_scpi_ports": tuple(sorted({5025, *args.raw_scpi_port})),
        "connect_timeout_s": args.connect_timeout,
        "idn_timeout_ms": args.idn_timeout_ms,
        "workers": args.workers,
        "max_network_hosts": args.max_hosts,
        "use_cache": not args.no_cache,
    }
    if args.cache is not None:
        config_kwargs["cache_path"] = args.cache

    report = discover_instruments(DiscoveryConfig(**config_kwargs))
    devices = report.identified_devices
    if not devices:
        print("No instruments identified")
    else:
        print(f"Identified {len(devices)} instrument(s):")
        for device in devices:
            identity = device.identity
            details = f"{identity.manufacturer}, {identity.model}"
            if identity.serial:
                details += f", serial {identity.serial}"
            if identity.firmware:
                details += f", firmware {identity.firmware}"
            print(f"- {details}")
            for resource in device.resources:
                print(f"  {resource}")

    failures = [attempt for attempt in report.attempts if attempt.outcome == "failed"]
    if args.show_failures and failures:
        print("Identification failures:")
        for attempt in failures:
            print(f"- {attempt.resource} [{attempt.source}]: {attempt.error}")

    if report.warnings:
        print("Discovery warnings:")
        for warning in report.warnings:
            print(f"- {warning}")
    return 0 if devices else 1


if __name__ == "__main__":
    sys.exit(main())
