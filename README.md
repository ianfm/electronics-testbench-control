# Electronics Testbench Control

SCPI-based drivers and helper scripts for common lab instruments (e.g., Rigol DM3058E DMM, Siglent SPD1305X/3303X PSUs). Uses PyVISA with the pure-Python backend by default.

## Quick start

1) **System prep (USB access, libusb, udev rules)**
   ```bash
   ./install/setup_system.sh
   # replug instruments after this
   ```

2) **Python env and package install**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

3) **Verify setup and diagnostics**
   - Run verification self-tests on all connected instruments:
     ```bash
     testbench verify
     ```
   - If a USB instrument hangs or fails with a pipe error, clear its connection:
     ```bash
     testbench reset-usb
     # Or run verification with a pre-reset:
     testbench verify --reset-usb
     ```
   - Top-level discovery can also be run directly:
     ```bash
     testbench discover
     ```

4) **Try a command**
   - Measure current with the DMM (defaults to Keithley 2110 adapter; swap imports in the script if needed):
     ```bash
     python3 examples/measure_current.py --num-samples 5
     ```
   - Nudge the Siglent SPD1305X supply:
     ```bash
     python3 examples/spd1305-module-test.py
     ```
   - Inspect M300 modules or measure one configured multiplexer channel:
     ```bash
     testbench daq --model rigol-m300 --list-modules
     testbench daq --model rigol-m300 --measure-voltage 201
     ```

## Notes
- The `install/99-visa.rules` udev rule plus `plugdev` group membership are required for non-root USB access; `install/setup_system.sh` applies them.
- If NI-VISA is installed, PyVISA will use it; otherwise `pyvisa-py` handles USB/LAN via libusb.
- Device drivers live under `src/testbench/**`; add new instruments by subclassing the domain interfaces in `src/testbench/domains/`.
- Discovery failure modes, fallback behavior, and scope controls are documented
  in [`docs/discovery.md`](docs/discovery.md).
- Model-specific programming references and their repository status are tracked
  in [`docs/instrument-documentation.md`](docs/instrument-documentation.md).
- Drivers can retain their independent lookup or accept a verified resource
  selected by the top-level discovery and binding registry.
- The M300 DAQ driver is manual-backed and clears its scan list after a
  single-channel measurement so the scanned multiplexer relay is left open.
- These instructions are tested on Ubuntu 22.04.
