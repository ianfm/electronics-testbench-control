"""Top-level mapping from discovered identities to model-specific drivers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from testbench.core.scpi import SCPISettings
from testbench.discovery import DeviceIdentity, DiscoveredDevice, DiscoveryReport
from testbench.daq.rigol_m300 import RigolM300
from testbench.dmm.keithley_2110 import Keithley2110
from testbench.dmm.rigol_dm3058e import RigolDM3058E
from testbench.psu.siglent_spd1305x import SiglentSPD1305X
from testbench.psu.siglent_spd3303x import SiglentSPD3303X
from testbench.sourcemeter.keysight_b2902b import KeysightB2902B


@dataclass(frozen=True)
class DriverRegistration:
    name: str
    domain: str
    driver_class: type
    manufacturer_terms: tuple[str, ...]
    model_names: tuple[str, ...]

    def matches(self, identity: DeviceIdentity) -> bool:
        manufacturer = identity.manufacturer.casefold()
        model = identity.model.casefold()
        return any(
            term.casefold() in manufacturer for term in self.manufacturer_terms
        ) and model in {name.casefold() for name in self.model_names}


DRIVER_REGISTRATIONS = (
    DriverRegistration("rigol-m300", "daq", RigolM300, ("rigol",), ("M300",)),
    DriverRegistration(
        "rigol-dm3058e", "dmm", RigolDM3058E, ("rigol",), ("DM3058E",)
    ),
    DriverRegistration(
        "keithley-2110", "dmm", Keithley2110, ("keithley",), ("2110", "MODEL 2110")
    ),
    DriverRegistration(
        "siglent-spd1305x", "psu", SiglentSPD1305X, ("siglent",), ("SPD1305X",)
    ),
    DriverRegistration(
        "siglent-spd3303x",
        "psu",
        SiglentSPD3303X,
        ("siglent",),
        ("SPD3303X", "SPD3303X-E"),
    ),
    DriverRegistration(
        "keysight-b2902b", "sourcemeter", KeysightB2902B, ("keysight",), ("B2902B",)
    ),
)


def registration_for_name(name: str) -> DriverRegistration:
    for registration in DRIVER_REGISTRATIONS:
        if registration.name == name:
            return registration
    raise KeyError(f"No driver registered as {name!r}")


def matching_device(
    driver_name: str,
    report: DiscoveryReport,
) -> Optional[DiscoveredDevice]:
    """Find a uniquely identified device matching a registered driver model."""
    registration = registration_for_name(driver_name)
    matches = [
        device
        for device in report.identified_devices
        if registration.matches(device.identity)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        serials = ", ".join(device.identity.serial or "<unknown>" for device in matches)
        raise RuntimeError(
            f"Multiple {driver_name} instruments found ({serials}); select a resource explicitly"
        )
    return matches[0]


def bind_driver(
    driver_name: str,
    device: DiscoveredDevice,
    settings: Optional[SCPISettings] = None,
):
    """Construct the matching driver using a verified discovered resource."""
    registration = registration_for_name(driver_name)
    if not registration.matches(device.identity):
        raise ValueError(
            f"{device.identity.raw!r} does not match registered driver {driver_name!r}"
        )
    kwargs = {"resource_name": device.preferred_resource}
    if settings is not None:
        kwargs["settings"] = settings
    return registration.driver_class(**kwargs)
