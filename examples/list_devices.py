"""
List discoverable VISA resources and attempt a *IDN? query for each.

Run from repo root after installing the package:
    python3 examples/list_devices.py

Optional: restrict discovery with --filter "USB?*" or similar.
"""

import argparse
import sys

import pyvisa


def main() -> int:
    parser = argparse.ArgumentParser(description="Enumerate SCPI/VISA resources")
    parser.add_argument(
        "--filter",
        default="",
        help='Query filter (e.g. "USB?*" or "TCPIP?*"); empty -> all resources',
    )
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="Include RAW USB resources (skip by default to avoid hangs)",
    )
    parser.add_argument(
        "--include-serial",
        action="store_true",
        help="Include ASRL/serial resources in output",
    )
    parser.add_argument(
        "--idn",
        action="store_true",
        help="Attempt *IDN? query for each resource",
    )
    args = parser.parse_args()

    rm = pyvisa.ResourceManager()
    resources = rm.list_resources(args.filter)
    if not resources:
        print("No VISA resources found")
        return 1

    print("Discovered resources:")
    for res in resources:
        if (not args.include_serial) and res.startswith(("ASRL", "PRLGX-ASRL")):
            continue
        if (not args.include_raw) and "::RAW" in res:
            continue
        print(f"- {res}")
        if args.idn:
            inst = None
            try:
                inst = rm.open_resource(res, write_termination="\n", timeout=2000)
                reply = inst.query("*IDN?").strip()
                print(f"  *IDN?: {reply}")
            except Exception as exc:
                print(f"  *IDN? failed: {exc}")
            finally:
                try:
                    if inst is not None:
                        inst.close()
                except Exception:
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
