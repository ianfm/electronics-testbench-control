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

3) **Verify discovery**
   ```bash
   python3 examples/list_devices.py --idn
   # should print INSTR entries for connected devices
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

## Notes
- The `install/99-visa.rules` udev rule plus `plugdev` group membership are required for non-root USB access; `install/setup_system.sh` applies them.
- If NI-VISA is installed, PyVISA will use it; otherwise `pyvisa-py` handles USB/LAN via libusb.
- Device drivers live under `src/testbench/**`; add new instruments by subclassing the domain interfaces in `src/testbench/domains/`.
- These instructions are tested on Ubuntu 22.04.
