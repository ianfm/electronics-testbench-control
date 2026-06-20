from __future__ import annotations

import usb.core

# Vendor IDs of instruments supported by the package:
# - 0x1ab1: Rigol (M300 / DM3058E)
# - 0x05e6: Keithley (2110)
# - 0x2a8d: Keysight (B2902B)
SUPPORTED_VENDORS = (0x1ab1, 0x05e6, 0x2a8d)


def reset_usb_instruments() -> dict[str, str]:
    """Locate all connected instrument USB devices and perform a hardware reset.

    Returns:
        A dictionary mapping device descriptions to their reset outcomes.
    """
    outcomes: dict[str, str] = {}

    for vendor_id in SUPPORTED_VENDORS:
        try:
            devices = usb.core.find(find_all=True, idVendor=vendor_id)
            for dev in devices:
                desc = f"USB Device (Vendor: 0x{vendor_id:04x}, Product: 0x{dev.idProduct:04x})"
                try:
                    dev.reset()
                    outcomes[desc] = "SUCCESS"
                except Exception as e:
                    outcomes[desc] = f"FAILED ({e})"
        except Exception as e:
            desc = f"Vendor Scan (0x{vendor_id:04x})"
            outcomes[desc] = f"SCAN FAILED ({e})"

    return outcomes
