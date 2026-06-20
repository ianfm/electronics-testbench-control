from __future__ import annotations

import os
from typing import Optional

from testbench.discovery import discover_instruments, DiscoveryReport

# Cache the discovery report globally across all tests in the same test runner process
_cached_report: Optional[DiscoveryReport] = None


def get_discovery_report() -> DiscoveryReport:
    """Return a cached discovery report, running discovery only once per process."""
    global _cached_report
    if _cached_report is None:
        print("\n[Hardware Test Suite] Running one-time auto-discovery of instruments...")
        _cached_report = discover_instruments()
    return _cached_report
