from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, TypeVar

import pyvisa


T = TypeVar("T")


@dataclass
class SCPISettings:
    """Connection and timing configuration shared across instruments."""

    query_delay: float = 0.05
    timeout_ms: int = 5000
    resource_filter: Optional[str] = None  # Example: "USB?*DM3*"


class SupportsSCPI(Protocol):
    """Protocol for SCPI-capable devices."""

    def write(self, command: str) -> None:
        ...

    def read_raw(self, size: int) -> bytes:
        ...

    def close(self) -> None:
        ...

    @property
    def write_termination(self) -> str:
        ...

    @write_termination.setter
    def write_termination(self, value: str) -> None:
        ...


class SCPIDriver:
    """Thin wrapper around pyvisa resources to standardize SCPI usage."""

    def __init__(self, settings: SCPISettings = SCPISettings()) -> None:
        self.settings = settings
        self.rm = pyvisa.ResourceManager()
        self.resource: Optional[SupportsSCPI] = None

    def list_matching_resources(self, substrings: Iterable[str]) -> list[str]:
        candidates = []
        # pyvisa/pyvisa-py expect an empty string rather than None for the query.
        query = self.settings.resource_filter or ""
        for r in self.rm.list_resources(query):
            if all(s in r for s in substrings):
                candidates.append(r)
        return candidates

    def connect_first(self, substrings: Iterable[str]) -> Optional[str]:
        """Connect to the first resource whose name contains all substrings."""
        pyvisa.query_delay = self.settings.query_delay
        matches = self.list_matching_resources(substrings)
        if not matches:
            return None
        name = matches[0]
        self.resource = self.rm.open_resource(name, timeout=self.settings.timeout_ms)
        self.resource.write_termination = "\n"
        return name

    def connect_resource(self, resource_name: str) -> str:
        """Connect directly to the provided VISA resource name."""
        pyvisa.query_delay = self.settings.query_delay
        self.resource = self.rm.open_resource(resource_name, timeout=self.settings.timeout_ms)
        self.resource.write_termination = "\n"
        return resource_name

    def write(self, command: str) -> None:
        if self.resource is None:
            raise RuntimeError("No SCPI resource connected")
        self.resource.write(command)

    def query(self, command: str, cast: type[T] | None = None) -> T | str:
        if self.resource is None:
            raise RuntimeError("No SCPI resource connected")
        self.write(command)
        raw = self.resource.read_raw(1024).decode("utf-8").strip()
        return cast(raw) if cast else raw

    def query_raw(self, command: str, size: int = 4096) -> bytes:
        """Send a query and return the raw byte payload without decoding."""
        if self.resource is None:
            raise RuntimeError("No SCPI resource connected")
        self.write(command)
        return self.resource.read_raw(size)

    def query_float_list(self, command: str) -> list[float]:
        """Send a query and parse the response as a comma-separated float list."""
        response = self.query(command)
        if isinstance(response, str):
            if not response:
                return []
            return [float(part) for part in response.split(",") if part.strip()]
        raise TypeError("query_float_list requires text response")

    @staticmethod
    def format_channel_list(channels: Iterable[int]) -> str:
        """Build ``(@1,2,...)`` syntax from a sequence of channels."""
        ch_list = list(channels)
        if not ch_list:
            raise ValueError("Channel list cannot be empty")
        if any(ch <= 0 for ch in ch_list):
            raise ValueError("Channels must be positive integers")
        inner = ",".join(str(ch) for ch in ch_list)
        return f"(@{inner})"

    def close(self) -> None:
        if self.resource:
            self.resource.close()
            self.resource = None

    def __enter__(self) -> "SCPIDriver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        with contextlib.suppress(Exception):
            self.close()
