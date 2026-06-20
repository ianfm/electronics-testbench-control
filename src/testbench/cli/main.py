# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from testbench.binding import DRIVER_REGISTRATIONS, bind_driver, matching_device
from testbench.core.formatters import format_engineering
from testbench.discovery import DiscoveryConfig, discover_instruments
from testbench.daq.rigol_m300 import RigolM300
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

DAQ_MODELS = {
    "rigol-m300": RigolM300,
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


def _add_resource_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--resource",
        help="Explicit VISA resource; bypasses independent and top-level discovery",
    )
    parser.add_argument(
        "--no-discovery-fallback",
        action="store_true",
        help="Do not run top-level discovery if the driver cannot find its resource",
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Testbench instrument helper CLI")
    sub = parser.add_subparsers(dest="domain", required=True)

    dmm = sub.add_parser("dmm", help="Digital multimeter actions")
    dmm.add_argument("--model", choices=DMM_MODELS.keys(), required=True)
    dmm.add_argument("--measure", choices=["voltage", "current", "resistance"])
    _add_resource_arguments(dmm)

    psu = sub.add_parser("psu", help="Power supply actions")
    psu.add_argument("--model", choices=PSU_MODELS.keys(), required=True)
    psu.add_argument("--channel", type=int, default=1)
    psu.add_argument("--set-voltage", type=float)
    psu.add_argument("--set-current", type=float)
    psu.add_argument("--power", choices=["on", "off"])
    psu.add_argument(
        "--measure",
        choices=["voltage", "current"],
        help="Measure output voltage or current (readback)",
    )
    _add_resource_arguments(psu)

    daq = sub.add_parser("daq", help="Data acquisition and switch actions")
    daq.add_argument("--model", choices=DAQ_MODELS.keys(), required=True)
    daq.add_argument("--list-modules", action="store_true")
    daq.add_argument("--measure-voltage", type=int, metavar="CHANNEL")
    _add_resource_arguments(daq)

    discover = sub.add_parser(
        "discover", help="Identify instruments using VISA and LAN fallbacks"
    )
    _add_discovery_arguments(discover)

    verify = sub.add_parser(
        "verify", help="Verify the bench setup by running self-tests on all connected instruments"
    )
    verify.add_argument(
        "--reset-usb",
        action="store_true",
        help="Perform a USB bus reset on all USB instruments before verifying",
    )

    sub.add_parser(
        "reset-usb", help="Reset all connected USB test instruments to clear locked endpoints"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args(argv)

    if args.domain == "discover":
        return _run_discovery(args)

    if args.domain == "verify":
        return _run_verification(args)

    if args.domain == "reset-usb":
        return _run_usb_reset()

    if args.domain == "dmm":
        cls = DMM_MODELS[args.model]
        dmm = _connect_model(cls, args.model, args)
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
        psu = _connect_model(cls, args.model, args)
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

        if args.measure == "voltage":
            val = psu.get_voltage(ch)
            if val is not None:
                print(f"{val} V")
            else:
                print("Failed to read voltage")
                return 1
        elif args.measure == "current":
            val = psu.get_current(ch)
            if val is not None:
                print(f"{val} A")
            else:
                print("Failed to read current")
                return 1

        if (
            args.set_voltage is None
            and args.set_current is None
            and args.power is None
            and args.measure is None
        ):
            print("No PSU action requested")
        return 0

    if args.domain == "daq":
        cls = DAQ_MODELS[args.model]
        daq = _connect_model(cls, args.model, args)
        if not daq.online():
            print("DAQ not found")
            return 1
        if args.list_modules:
            for slot, module in daq.module_inventory().items():
                print(
                    f"{slot}: {module.manufacturer}, {module.model}, "
                    f"{module.serial}, {module.firmware}"
                )
        if args.measure_voltage is not None:
            reading = daq.measure_voltage_dc(args.measure_voltage)
            print(f"{reading} V")
        if not args.list_modules and args.measure_voltage is None:
            print("No DAQ action requested")
        return 0

    return 1


def _connect_model(cls, model_name: str, args: argparse.Namespace):
    """Give model discovery first chance, then bind a top-level discovery result."""
    if args.resource:
        return cls(resource_name=args.resource)

    instrument = cls()
    if instrument.online() or args.no_discovery_fallback:
        return instrument

    report = discover_instruments()
    device = matching_device(model_name, report)
    if device is None:
        return instrument

    # The first instance did not connect. Release its resource manager before
    # constructing a driver around the verified endpoint.
    try:
        instrument.driver.rm.close()
    except Exception:
        pass
    return bind_driver(model_name, device)


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

    # Check for stale USB devices
    stale_usb_detected = any(
        attempt.outcome == "failed" and attempt.resource.startswith("USB")
        for attempt in report.attempts
    )
    if stale_usb_detected:
        print("\nWARNING: One or more USB instruments failed to respond to discovery.")
        print("Recommendation: Run 'testbench reset-usb' to clear locked USB endpoints.")

    return 0 if devices else 1


def _guess_driver_for_resource(resource: str) -> str | None:
    """Guess which registered driver matches a failed VISA resource based on ID strings."""
    if "::6833::" in resource or "::2500::" in resource or "::1ab1::" in resource or "DM3" in resource:
        return "rigol-dm3058e"
    if "::1510::" in resource or "::8464::" in resource or "05e6" in resource:
        return "keithley-2110"
    if "::62700::" in resource or "f4ec" in resource:
        return "siglent-spd3303x"
    if "M300" in resource:
        return "rigol-m300"
    return None


def _run_verification(args: argparse.Namespace) -> int:
    import os

    if args.reset_usb:
        from testbench.core.usb_reset import reset_usb_instruments
        print("Resetting USB instruments on the bench...")
        reset_usb_instruments()

    print("Discovering bench instruments...")
    report = discover_instruments()

    verified_any = False
    failed_any = False

    print("\n" + "=" * 80)
    print(f"{'Instrument':<20} | {'Status':<8} | {'Resource':<30} | {'Self-Test Reading'}")
    print("-" * 80)

    for reg in DRIVER_REGISTRATIONS:
        # Check environment variable overrides (consistent with hardware tests)
        env_var = f"TEST_HW_RESOURCE_{reg.name.replace('-', '_').upper()}"
        resource_override = os.environ.get(env_var)

        device = None
        resource_name = None

        if resource_override:
            resource_name = resource_override
        else:
            try:
                device = matching_device(reg.name, report)
            except Exception:
                pass

        # If not identified via matching_device, check if any failed attempts
        # correspond to this driver registration.
        matching_failure = None
        if not device and not resource_name:
            for attempt in report.attempts:
                if attempt.outcome == "failed":
                    if _guess_driver_for_resource(attempt.resource) == reg.name:
                        matching_failure = attempt
                        break

            if not matching_failure:
                continue

        verified_any = True
        status = "FAIL"
        details = ""
        endpoint = ""

        if matching_failure:
            failed_any = True
            details = f"Failed in discovery: {matching_failure.error}"
            status = "FAIL"
            endpoint = matching_failure.resource
            print(f"{reg.name:<20} | {status:<8} | {endpoint[:28]:<30} | {details}")
            continue

        try:
            if resource_name:
                driver_instance = reg.driver_class(resource_name=resource_name)
                endpoint = resource_name
            else:
                driver_instance = bind_driver(reg.name, device)
                endpoint = driver_instance.instrument_name

            if not driver_instance.online():
                details = "Offline"
            else:
                # Run safe read-only verification
                if reg.domain == "dmm":
                    val = driver_instance.measure_voltage_dc()
                    details = f"Voltage: {format_engineering(val, 'V')}"
                elif reg.domain == "daq":
                    # MC3132 multiplexer card in slot 200, check channel 201
                    val = driver_instance.measure_voltage_dc(201)
                    details = f"Ch 201: {format_engineering(val, 'V')}"
                elif reg.domain == "psu":
                    val = driver_instance.get_voltage(1)
                    if val is not None:
                        details = f"Ch 1: {format_engineering(val, 'V')}"
                    else:
                        details = "No readback response"
                else:
                    details = driver_instance.identify().strip()
                status = "PASS"

            # Close connection
            try:
                driver_instance.driver.close()
            except Exception:
                pass
        except Exception as e:
            failed_any = True
            details = f"Error: {e}"
            status = "FAIL"
            endpoint = resource_name or (device.resources[0] if device and device.resources else "Unknown")

        print(f"{reg.name:<20} | {status:<8} | {endpoint[:28]:<30} | {details}")

    print("=" * 80)

    # Check for stale USB devices
    stale_usb_detected = any(
        attempt.outcome == "failed" and attempt.resource.startswith("USB")
        for attempt in report.attempts
    )
    if stale_usb_detected:
        print("\nWARNING: One or more USB instruments failed to respond to discovery.")
        print("Recommendation: Run verification with the --reset-usb flag:")
        print("    testbench verify --reset-usb")

    if not verified_any:
        print("No registered instruments were found or configured.")
        return 1

    if failed_any:
        print("Verification completed with self-test failures.")
        return 1

    print("Verification completed successfully. All self-tests passed!")
    return 0


def _run_usb_reset() -> int:
    from testbench.core.usb_reset import reset_usb_instruments

    print("Scanning USB bus for supported testbench instruments...")
    results = reset_usb_instruments()
    if not results:
        print("No supported USB instruments found on the bus.")
        return 0

    print("\nReset results:")
    failed = False
    for desc, outcome in results.items():
        print(f"- {desc}: {outcome}")
        if "FAILED" in outcome:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
