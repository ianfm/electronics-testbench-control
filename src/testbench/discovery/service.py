from __future__ import annotations

import ipaddress
import json
import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import psutil
import pyvisa


DEFAULT_RAW_SCPI_PORTS = (5025,)


def default_cache_path() -> Path:
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "electronics-testbench-control" / "discovery.json"


@dataclass(frozen=True)
class DeviceIdentity:
    """Parsed response to the standard, read-only ``*IDN?`` query."""

    raw: str
    manufacturer: str
    model: str
    serial: str
    firmware: str

    @classmethod
    def parse(cls, response: str) -> "DeviceIdentity":
        raw = response.strip().strip("\x00")
        if not raw:
            raise ValueError("empty *IDN? response")
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) < 2:
            raise ValueError(f"response is not a recognizable *IDN? tuple: {raw!r}")
        return cls(
            raw=raw,
            manufacturer=parts[0],
            model=parts[1],
            serial=parts[2] if len(parts) > 2 else "",
            firmware=",".join(parts[3:]) if len(parts) > 3 else "",
        )

    @property
    def key(self) -> str:
        manufacturer = self.manufacturer.casefold()
        if self.serial:
            return f"serial:{manufacturer}:{self.serial.casefold()}"
        return f"idn:{self.raw.casefold()}"


@dataclass
class DiscoveryAttempt:
    resource: str
    source: str
    outcome: str
    idn: Optional[str] = None
    error: Optional[str] = None


@dataclass
class DiscoveredDevice:
    identity: DeviceIdentity
    resources: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def add_endpoint(self, resource: str, source: str) -> None:
        if resource not in self.resources:
            self.resources.append(resource)
        if source not in self.sources:
            self.sources.append(source)


@dataclass
class DiscoveryReport:
    devices: dict[str, DiscoveredDevice] = field(default_factory=dict)
    attempts: list[DiscoveryAttempt] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def record(
        self,
        resource: str,
        source: str,
        identity: Optional[DeviceIdentity] = None,
        error: Optional[str] = None,
    ) -> None:
        if identity is None:
            self.attempts.append(
                DiscoveryAttempt(resource, source, "failed", error=error or "unknown error")
            )
            return

        self.attempts.append(
            DiscoveryAttempt(resource, source, "identified", idn=identity.raw)
        )
        device = self.devices.setdefault(identity.key, DiscoveredDevice(identity))
        device.add_endpoint(resource, source)

    @property
    def identified_devices(self) -> list[DiscoveredDevice]:
        return sorted(
            self.devices.values(),
            key=lambda device: (
                device.identity.manufacturer.casefold(),
                device.identity.model.casefold(),
                device.identity.serial.casefold(),
            ),
        )


@dataclass
class DiscoveryConfig:
    """Controls discovery scope, timeouts, and persistence."""

    visa_filter: str = ""
    include_raw_usb: bool = False
    include_serial: bool = False
    scan_network: bool = True
    subnets: tuple[str, ...] = ()
    raw_scpi_ports: tuple[int, ...] = DEFAULT_RAW_SCPI_PORTS
    connect_timeout_s: float = 0.25
    idn_timeout_ms: int = 2000
    query_attempts: int = 2
    workers: int = 64
    max_network_hosts: int = 1024
    use_cache: bool = True
    cache_path: Path = field(default_factory=default_cache_path)


def discover_instruments(
    config: Optional[DiscoveryConfig] = None,
    resource_manager=None,
) -> DiscoveryReport:
    """Identify instruments using cache, VISA enumeration, and LAN fallbacks.

    Discovery sends only ``*IDN?``. It does not reset, configure, trigger, or
    enable an instrument output.
    """

    config = config or DiscoveryConfig()
    _validate_config(config)
    report = DiscoveryReport()
    rm = resource_manager or pyvisa.ResourceManager()
    owns_rm = resource_manager is None
    probed: set[str] = set()

    try:
        if config.use_cache:
            for resource in _load_cached_resources(config.cache_path, report):
                _probe_once(resource, "cache", rm, config, report, probed)

        try:
            resources = rm.list_resources(config.visa_filter)
        except Exception as exc:
            report.warnings.append(f"VISA resource enumeration failed: {exc}")
            resources = ()

        for resource in resources:
            if not config.include_serial and resource.startswith(("ASRL", "PRLGX-ASRL")):
                continue
            if not config.include_raw_usb and "::RAW" in resource:
                continue
            _probe_once(resource, "visa", rm, config, report, probed)

        if config.scan_network:
            networks = _networks_to_scan(config, report)
            ports = tuple(sorted({111, *config.raw_scpi_ports}))
            open_ports = _scan_open_ports(
                networks,
                ports,
                timeout_s=config.connect_timeout_s,
                workers=config.workers,
            )
            for address, port in open_ports:
                if port == 111:
                    _probe_once(
                        f"TCPIP::{address}::INSTR",
                        "vxi11-subnet-scan",
                        rm,
                        config,
                        report,
                        probed,
                    )
                if port in config.raw_scpi_ports:
                    _probe_once(
                        f"TCPIP::{address}::{port}::SOCKET",
                        "scpi-socket-subnet-scan",
                        rm,
                        config,
                        report,
                        probed,
                    )

        if config.use_cache:
            _save_cache(config.cache_path, report)
    finally:
        if owns_rm:
            try:
                rm.close()
            except Exception:
                pass

    return report


def _probe_once(
    resource: str,
    source: str,
    rm,
    config: DiscoveryConfig,
    report: DiscoveryReport,
    probed: set[str],
) -> None:
    if resource in probed:
        return
    probed.add(resource)
    identity, error = _query_identity(rm, resource, config)
    report.record(resource, source, identity=identity, error=error)


def _query_identity(
    rm,
    resource: str,
    config: DiscoveryConfig,
) -> tuple[Optional[DeviceIdentity], Optional[str]]:
    errors: list[str] = []
    for _ in range(max(1, config.query_attempts)):
        instrument = None
        try:
            instrument = rm.open_resource(
                resource,
                open_timeout=config.idn_timeout_ms,
                timeout=config.idn_timeout_ms,
                write_termination="\n",
                read_termination="\n",
            )
            response = instrument.query("*IDN?")
            return DeviceIdentity.parse(response), None
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            if instrument is not None:
                try:
                    instrument.close()
                except Exception:
                    pass
    return None, "; ".join(errors)


def _networks_to_scan(
    config: DiscoveryConfig,
    report: DiscoveryReport,
) -> list[ipaddress.IPv4Network]:
    candidates: list[ipaddress.IPv4Network] = []
    if config.subnets:
        for value in config.subnets:
            try:
                network = ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                report.warnings.append(f"Skipped invalid subnet {value!r}: {exc}")
                continue
            if not isinstance(network, ipaddress.IPv4Network):
                report.warnings.append(f"Skipped non-IPv4 subnet: {value}")
                continue
            candidates.append(network)
    else:
        candidates = _local_ipv4_networks()

    networks: list[ipaddress.IPv4Network] = []
    for network in candidates:
        if not isinstance(network, ipaddress.IPv4Network):
            continue
        usable_hosts = max(0, network.num_addresses - 2)
        if usable_hosts > config.max_network_hosts:
            report.warnings.append(
                f"Skipped {network}: {usable_hosts} hosts exceeds "
                f"--max-hosts={config.max_network_hosts}; pass a narrower --subnet"
            )
            continue
        if network not in networks:
            networks.append(network)
    return networks


def _validate_config(config: DiscoveryConfig) -> None:
    if config.connect_timeout_s <= 0:
        raise ValueError("connect_timeout_s must be positive")
    if config.idn_timeout_ms <= 0:
        raise ValueError("idn_timeout_ms must be positive")
    if config.query_attempts <= 0:
        raise ValueError("query_attempts must be positive")
    if config.workers <= 0:
        raise ValueError("workers must be positive")
    if config.max_network_hosts <= 0:
        raise ValueError("max_network_hosts must be positive")
    invalid_ports = [port for port in config.raw_scpi_ports if not 1 <= port <= 65535]
    if invalid_ports:
        raise ValueError(f"raw SCPI ports must be between 1 and 65535: {invalid_ports}")


def _local_ipv4_networks() -> list[ipaddress.IPv4Network]:
    networks: list[ipaddress.IPv4Network] = []
    stats = psutil.net_if_stats()
    for interface, addresses in psutil.net_if_addrs().items():
        if interface == "lo" or not stats.get(interface) or not stats[interface].isup:
            continue
        for address in addresses:
            if address.family != socket.AF_INET or not address.netmask:
                continue
            ip = ipaddress.ip_address(address.address)
            if ip.is_loopback or ip.is_link_local:
                continue
            network = ipaddress.ip_network(f"{address.address}/{address.netmask}", strict=False)
            if network not in networks:
                networks.append(network)
    return networks


def _scan_open_ports(
    networks: Iterable[ipaddress.IPv4Network],
    ports: Iterable[int],
    timeout_s: float,
    workers: int,
) -> list[tuple[str, int]]:
    targets = [
        (str(host), port)
        for network in networks
        for host in network.hosts()
        for port in ports
    ]
    if not targets:
        return []

    found: list[tuple[str, int]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(_port_is_open, address, port, timeout_s): (address, port)
            for address, port in targets
        }
        for future in as_completed(futures):
            address, port = futures[future]
            try:
                if future.result():
                    found.append((address, port))
            except OSError:
                pass
    return sorted(found, key=lambda item: (ipaddress.ip_address(item[0]), item[1]))


def _port_is_open(address: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.create_connection((address, port), timeout=max(0.01, timeout_s)):
            return True
    except OSError:
        return False


def _load_cached_resources(path: Path, report: DiscoveryReport) -> list[str]:
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("resources"), list):
            report.warnings.append(f"Ignored unsupported discovery cache: {path}")
            return []
        return [value for value in data["resources"] if isinstance(value, str)]
    except Exception as exc:
        report.warnings.append(f"Could not read discovery cache {path}: {exc}")
        return []


def _save_cache(path: Path, report: DiscoveryReport) -> None:
    resources = sorted(
        {
            resource
            for device in report.identified_devices
            for resource in device.resources
        }
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "resources": resources}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception as exc:
        report.warnings.append(f"Could not write discovery cache {path}: {exc}")
