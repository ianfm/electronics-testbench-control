"""Compatibility wrapper for the layered instrument discovery command."""

import sys

from testbench.cli.main import main as testbench_main


def main() -> int:
    return testbench_main(["discover", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
