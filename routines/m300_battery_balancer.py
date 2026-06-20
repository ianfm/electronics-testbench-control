#!/usr/bin/env python3
"""LiFePO4 Cell Balancer Application.

Controls a Rigol M300 (with MC3132 MUX in slot 200 and MC3416 ACT in slot 300)
and a Siglent SPD3303X PSU to monitor and balance 16 connected LiFePO4 cells.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List

from testbench.binding import bind_driver, matching_device
from testbench.core.formatters import format_engineering
from testbench.daq.rigol_m300 import RigolM300
from testbench.discovery import discover_instruments
from testbench.psu.siglent_spd3303x import SiglentSPD3303X

# ANSI Color Codes for terminal UI
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_BLUE = "\033[94m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"
CLEAR_SCREEN = "\033[H\033[J"


@dataclass
class BatchState:
    name: str
    psu_channel: int
    mux_channels: List[int]
    act_channels: List[int]
    status: str = "MONITORING"  # MONITORING, CHARGING, COMPLETED
    active_cells: List[int] = None  # 1-indexed cell numbers in this batch (1-8 or 9-16)
    charge_mode: str = "OFF"  # OFF, RECOVERY, BULK, BALANCING
    voltage_set: float = 0.0
    current_set: float = 0.0
    current_reading: float = 0.0

    def __post_init__(self):
        if self.active_cells is None:
            self.active_cells = []


class BalancerApp:
    def __init__(
        self,
        target_voltage: float = 3.60,
        balance_delta: float = 0.02,
        parallel_delta: float = 0.01,
        simulated: bool = False,
        interval: float = 5.0,
        mode: str = "single",
        max_voltage: float = 3.65,
        max_current: float = 3.2,
        max_cell_current: float = 1.2,
        batches: str = "both",
    ) -> None:
        self.target_voltage = target_voltage
        self.balance_delta = balance_delta
        self.parallel_delta = parallel_delta
        self.simulated = simulated
        self.interval = interval
        self.mode = mode
        self.max_voltage = max_voltage
        self.max_current = max_current
        self.max_cell_current = max_cell_current
        self.batches = batches

        self.daq: RigolM300 | None = None
        self.psu: SiglentSPD3303X | None = None

        # Setup batch configurations
        if batches == "4/4":
            # 4 sequential cells on Batch 1 (PSU CH1), 4 sequential cells on Batch 2 (PSU CH2)
            self.batch1 = BatchState(
                name="Batch 1 (Cells 1-4)",
                psu_channel=1,
                mux_channels=list(range(201, 205)),
                act_channels=list(range(301, 305)),
            )
            self.batch2 = BatchState(
                name="Batch 2 (Cells 5-8)",
                psu_channel=2,
                mux_channels=list(range(205, 209)),
                act_channels=list(range(305, 309)),
            )
        else:
            # Standard 8/8 split
            # Batch 1: Cells 1-8, MUX 201-208, ACT 301-308, PSU CH1
            self.batch1 = BatchState(
                name="Batch 1 (Cells 1-8)",
                psu_channel=1,
                mux_channels=list(range(201, 209)),
                act_channels=list(range(301, 309)),
            )
            # Batch 2: Cells 9-16, MUX 209-216, ACT 309-316, PSU CH2
            self.batch2 = BatchState(
                name="Batch 2 (Cells 9-16)",
                psu_channel=2,
                mux_channels=list(range(209, 217)),
                act_channels=list(range(309, 317)),
            )

        if batches == "1":
            self.batch2.status = "COMPLETED"
            self.batch2.charge_mode = "OFF"
        elif batches == "2":
            self.batch1.status = "COMPLETED"
            self.batch1.charge_mode = "OFF"

        # Initialize cell voltages (used for simulation)
        self.sim_voltages = [
            2.90, 3.22, 3.25, 3.21, 3.24, 3.20, 3.23, 3.26,  # Batch 1 (Cell 1 is low)
            3.05, 3.22, 3.28, 3.21, 3.25, 3.19, 3.24, 3.30,  # Batch 2 (Cell 9 is low)
        ]

    def connect(self) -> None:
        if self.simulated:
            print("Running in SIMULATION mode.")
            return

        try:
            print("Discovering bench instruments...")
            report = discover_instruments()

            # Bind M300
            daq_device = matching_device("rigol-m300", report)
            if not daq_device:
                raise RuntimeError("Rigol M300 not found on the bench.")
            self.daq = bind_driver("rigol-m300", daq_device)

            # Bind SPD3303X
            psu_device = matching_device("siglent-spd3303x", report)
            if not psu_device:
                raise RuntimeError("Siglent SPD3303X PSU not found on the bench.")
            self.psu = bind_driver("siglent-spd3303x", psu_device)

            print(f"Connected to DAQ: {self.daq.identify().strip()}")
            print(f"Connected to PSU: {self.psu.identify().strip()}")

            # Make sure DMM is enabled
            if not self.daq.dmm_installed() or not self.daq.dmm_enabled():
                raise RuntimeError("M300 DMM card is not installed or enabled.")

        except Exception as e:
            print(f"{COLOR_RED}Error connecting to hardware: {e}{COLOR_RESET}")
            print("Defaulting to SIMULATION mode.")
            self.simulated = True
            time.sleep(2.0)

    def shutdown(self) -> None:
        """Safely open all gates and turn off the PSU channels."""
        print("\nInitiating safety shutdown...")
        if self.simulated:
            self.batch1.status = "COMPLETED"
            self.batch2.status = "COMPLETED"
            self.batch1.charge_mode = "OFF"
            self.batch2.charge_mode = "OFF"
            print("Simulation safety shutdown complete.")
            return

        # Turn off PSU channels first
        if self.psu:
            try:
                self.psu.output_off(1)
                self.psu.output_off(2)
                print("PSU outputs disabled.")
            except Exception as e:
                print(f"Failed to turn off PSU outputs: {e}")

        # Open all M300 ACT channels
        if self.daq:
            try:
                # Open slots 300 actuator channels
                self.daq.open_channels(range(301, 317))
                self.daq.clear_scan()
                print("All actuator relays opened.")
            except Exception as e:
                print(f"Failed to open M300 actuator relays: {e}")

    def measure_cell_voltages(self, batch: BatchState) -> List[float]:
        """Measure cell voltages in a batch after opening the charge relays to settle."""
        if self.simulated:
            # Return simulated voltages for this batch
            if batch.psu_channel == 1:
                indices = range(0, len(batch.mux_channels))
            else:
                n1 = len(self.batch1.mux_channels)
                indices = range(n1, n1 + len(batch.mux_channels))
            return [self.sim_voltages[i] for i in indices]

        # 1. Open charge relays for this batch to ensure we measure open-circuit/settled voltage
        self.daq.open_channels(batch.act_channels)
        time.sleep(1.0)  # Wait 1 second for voltage to settle

        # 2. Scan cell voltages using the MUX
        self.daq.setup_scan(batch.mux_channels)
        voltages = self.daq.read_scan()
        self.daq.clear_scan()

        return voltages

    def calculate_cell_currents(
        self,
        voltages: List[float],
        active_cells: List[int],
        total_current: float,
        internal_resistance: float = 0.05
    ) -> List[float]:
        """Calculate the shared current flowing into each active cell using a parallel impedance model."""
        if not active_cells:
            return [0.0] * len(voltages)

        M = len(active_cells)
        active_voltages = [voltages[cell_num - 1] for cell_num in active_cells]
        sum_v = sum(active_voltages)

        # V_common = (I_total * R + sum(V_j)) / M
        v_common = (total_current * internal_resistance + sum_v) / M

        cell_currents = [0.0] * len(voltages)
        for cell_num in active_cells:
            idx = cell_num - 1
            # I_i = (V_common - V_i) / R
            i_i = (v_common - voltages[idx]) / internal_resistance
            cell_currents[idx] = i_i

        return cell_currents

    def update_batch_state(self, batch: BatchState, voltages: List[float]) -> None:
        """Execute state machine step for a single batch."""
        if all(v >= self.target_voltage for v in voltages):
            batch.status = "COMPLETED"
            batch.charge_mode = "OFF"
            batch.active_cells = []
            batch.voltage_set = 0.0
            batch.current_set = 0.0
            batch.current_reading = 0.0
            if not self.simulated:
                self.psu.output_off(batch.psu_channel)
                self.daq.open_channels(batch.act_channels)
            return

        # Find weakest and strongest cells
        v_min = min(voltages)
        v_max = max(voltages)

        # Select cells to charge:
        # If running in "single" mode, only charge the single weakest cell.
        # Otherwise, in "parallel" mode, charge all cells within parallel_delta of the weakest cell.
        active_indices = []
        if self.mode == "single":
            if v_min < self.target_voltage:
                weakest_idx = voltages.index(v_min)
                active_indices = [weakest_idx]
        else:
            for idx, v in enumerate(voltages):
                if v < self.target_voltage and v < (v_min + self.parallel_delta):
                    active_indices.append(idx)

        # Map batch indices to global cell numbers (e.g. 1-16 or 1-8)
        offset = 0 if batch.psu_channel == 1 else len(self.batch1.mux_channels)
        batch.active_cells = [idx + 1 + offset for idx in active_indices]

        if not batch.active_cells:
            # If no cells selected, default to the single weakest cell
            weakest_idx = voltages.index(v_min)
            batch.active_cells = [weakest_idx + 1 + offset]
            active_indices = [weakest_idx]

        # Determine charge rates based on weakest cell voltage
        if v_min < 3.0:
            batch.charge_mode = "RECOVERY"
            batch.voltage_set = 3.30
            i_cell = 0.2  # Gentle recovery current
        elif v_min < 3.45:
            batch.charge_mode = "BULK"
            batch.voltage_set = 3.60
            i_cell = 1.0  # Bulk charge current
        else:
            batch.charge_mode = "BALANCING"
            batch.voltage_set = 3.62
            i_cell = 0.3  # Precision top-off/balancing current

        # Scale current based on number of active cells in parallel
        # Max out at self.max_current - 0.1 to avoid spurious safety trips from readback noise
        max_setpoint = self.max_current - 0.1
        batch.current_set = min(max_setpoint, len(batch.active_cells) * i_cell)

        # Dynamic calculated cell current limiting in parallel mode:
        if self.mode == "parallel" and len(batch.active_cells) > 1:
            R = 0.05  # internal cell + connection resistance
            active_voltages = [voltages[idx] for idx in active_indices]
            sum_v = sum(active_voltages)
            M = len(active_voltages)

            # Limit total current so that the lowest cell current doesn't exceed self.max_cell_current
            i_total_limit = M * self.max_cell_current - (sum_v - M * v_min) / R
            i_total_limit = max(0.0, i_total_limit)
            batch.current_set = min(batch.current_set, i_total_limit)

        batch.status = "CHARGING"

        # Apply settings to hardware or simulate
        if self.simulated:
            batch.current_reading = batch.current_set * random.uniform(0.95, 1.02)
        else:
            # Configure PSU
            self.psu.set_voltage(batch.psu_channel, batch.voltage_set)
            self.psu.set_current(batch.psu_channel, batch.current_set)
            self.psu.output_on(batch.psu_channel)

            # Map active indices to actual ACT channels
            active_acts = [batch.act_channels[idx] for idx in active_indices]
            inactive_acts = [ch for ch in batch.act_channels if ch not in active_acts]

            # Set relays
            self.daq.open_channels(inactive_acts)
            self.daq.close_channels(active_acts)

            # Get actual current readback
            curr = self.psu.get_current(batch.psu_channel)
            batch.current_reading = curr if curr is not None else 0.0

    def simulate_step(self, dt: float) -> None:
        """Simulate battery cell charging physics."""
        n1 = len(self.batch1.mux_channels)
        n2 = len(self.batch2.mux_channels)

        # Calculate currents using the parallel impedance model
        currents1 = self.calculate_cell_currents(
            self.sim_voltages[:n1], self.batch1.active_cells, self.batch1.current_reading
        )
        currents2 = self.calculate_cell_currents(
            self.sim_voltages[n1:n1+n2], [c - n1 for c in self.batch2.active_cells], self.batch2.current_reading
        )

        for idx in range(n1 + n2):
            i_cell = (currents1[idx] if idx < n1 else currents2[idx - n1])
            # Voltage rises proportionally to calculated cell current * dt
            self.sim_voltages[idx] += i_cell * dt * 0.005
            
            # Cap voltage at the target charging voltage for the batch
            v_set = self.batch1.voltage_set if idx < n1 else self.batch2.voltage_set
            if self.sim_voltages[idx] > v_set:
                self.sim_voltages[idx] = v_set

    def print_dashboard(self, voltages: List[float]) -> None:
        """Render a beautiful, real-time balancing dashboard in the terminal."""
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.write(f"{COLOR_BOLD}{COLOR_BLUE}=== LiFePO4 Battery Cell 16-Channel Balancer ==={COLOR_RESET}\n")
        sys.stdout.write(f"Target Voltage: {COLOR_BOLD}{self.target_voltage:.3f} V{COLOR_RESET} | ")
        sys.stdout.write(f"Mode: {COLOR_BOLD}{self.mode.upper()}{COLOR_RESET} | ")
        sys.stdout.write(f"Parallel Delta: {COLOR_BOLD}{self.parallel_delta * 1000:.0f} mV{COLOR_RESET}\n")
        sys.stdout.write("=" * 70 + "\n\n")

        # Helper to draw progress bars
        def get_bar(v: float) -> str:
            min_v, max_v = 2.5, self.target_voltage
            pct = max(0.0, min(1.0, (v - min_v) / (max_v - min_v)))
            filled = int(pct * 20)
            bar_str = "█" * filled + "░" * (20 - filled)
            color = COLOR_RED if v < 3.0 else COLOR_YELLOW if v < 3.45 else COLOR_GREEN
            return f"{color}[{bar_str}]{COLOR_RESET} {pct*100:5.1f}%"

        def print_batch(batch: BatchState, offset: int):
            color = COLOR_GREEN if batch.status == "COMPLETED" else COLOR_CYAN if batch.status == "CHARGING" else COLOR_YELLOW
            sys.stdout.write(f"{COLOR_BOLD}{color}{batch.name}{COLOR_RESET}\n")
            sys.stdout.write(f"Status: {COLOR_BOLD}{batch.status}{COLOR_RESET} | Mode: {COLOR_BOLD}{batch.charge_mode}{COLOR_RESET}\n")
            if batch.status == "CHARGING":
                sys.stdout.write(f"PSU: {batch.voltage_set:.3f} V | Limit: {batch.current_set:.3f} A | Readback: {COLOR_YELLOW}{batch.current_reading:.3f} A{COLOR_RESET}\n")
            else:
                sys.stdout.write("PSU output: OFF\n")
            sys.stdout.write("-" * 70 + "\n")

            for i in range(len(batch.mux_channels)):
                cell_idx = offset + i
                cell_num = cell_idx + 1
                v = voltages[cell_idx]
                bar = get_bar(v)
                active_str = f"{COLOR_BOLD}{COLOR_YELLOW}(Charging){COLOR_RESET}" if cell_num in (batch.active_cells or []) else "          "
                sys.stdout.write(f"Cell {cell_num:02d}: {v:.4f} V {bar} {active_str}\n")
            sys.stdout.write("\n")

        print_batch(self.batch1, 0)
        print_batch(self.batch2, len(self.batch1.mux_channels))

    def step(self) -> bool:
        """Executes a single monitoring, control, and safety check step.

        Returns:
            True if charging/balancing is completed for both batches, False otherwise.
        """
        # 1. Measure cell voltages for active batches
        v1 = self.measure_cell_voltages(self.batch1) if self.batch1.status != "COMPLETED" else [0.0] * 8
        v2 = self.measure_cell_voltages(self.batch2) if self.batch2.status != "COMPLETED" else [0.0] * 8
        all_voltages = v1 + v2

        # Safety Check: Individual cell overvoltage protection
        for idx, v in enumerate(all_voltages):
            batch_completed = self.batch1.status == "COMPLETED" if idx < 8 else self.batch2.status == "COMPLETED"
            if not batch_completed and v > self.max_voltage:
                raise ValueError(
                    f"CRITICAL SAFETY VIOLATION: Cell {idx + 1} voltage {v:.3f} V "
                    f"exceeded maximum safety limit of {self.max_voltage:.3f} V! "
                    f"Aborting immediately."
                )

        # 2. Update state and outputs
        if self.batch1.status != "COMPLETED":
            self.update_batch_state(self.batch1, v1)
        if self.batch2.status != "COMPLETED":
            self.update_batch_state(self.batch2, v2)

        # Safety Check: PSU channel total overcurrent protection (strict limit)
        if self.batch1.status != "COMPLETED" and self.batch1.current_reading > self.max_current:
            raise ValueError(
                f"CRITICAL SAFETY VIOLATION: PSU Channel 1 current {self.batch1.current_reading:.3f} A "
                f"exceeded safety limit of {self.max_current:.3f} A! "
                f"Aborting immediately."
            )
        if self.batch2.status != "COMPLETED" and self.batch2.current_reading > self.max_current:
            raise ValueError(
                f"CRITICAL SAFETY VIOLATION: PSU Channel 2 current {self.batch2.current_reading:.3f} A "
                f"exceeded safety limit of {self.max_current:.3f} A! "
                f"Aborting immediately."
            )

        # Safety Check: Calculated individual cell overcurrent protection
        if self.batch1.status != "COMPLETED":
            currents1 = self.calculate_cell_currents(
                v1, self.batch1.active_cells, self.batch1.current_reading
            )
            for idx in range(len(v1)):
                if currents1[idx] > self.max_cell_current:
                    raise ValueError(
                        f"CRITICAL SAFETY VIOLATION: Calculated current for Cell {idx + 1} is {currents1[idx]:.3f} A, "
                        f"exceeded individual cell current safety limit of {self.max_cell_current:.3f} A! "
                        f"Aborting immediately."
                    )

        if self.batch2.status != "COMPLETED":
            n1 = len(self.batch1.mux_channels)
            currents2 = self.calculate_cell_currents(
                v2, [c - n1 for c in self.batch2.active_cells], self.batch2.current_reading
            )
            for idx in range(len(v2)):
                if currents2[idx] > self.max_cell_current:
                    raise ValueError(
                        f"CRITICAL SAFETY VIOLATION: Calculated current for Cell {idx + n1 + 1} is {currents2[idx]:.3f} A, "
                        f"exceeded individual cell current safety limit of {self.max_cell_current:.3f} A! "
                        f"Aborting immediately."
                    )

        # 3. Render dashboard
        self.print_dashboard(all_voltages)

        if self.batch1.status == "COMPLETED" and self.batch2.status == "COMPLETED":
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_GREEN}Balancing complete! All cells charged successfully.{COLOR_RESET}\n")
            return True

        return False

    def run(self) -> None:
        """Main application loop."""
        self.connect()

        try:
            while True:
                complete = self.step()
                if complete:
                    break

                # 4. Wait / Simulate charging
                if self.simulated:
                    # In simulation, step faster than real time for better UX
                    sim_steps = int(self.interval)
                    for _ in range(sim_steps):
                        self.simulate_step(1.0)
                        time.sleep(0.2)
                else:
                    time.sleep(self.interval)

        except KeyboardInterrupt:
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_RED}Operation interrupted by user.{COLOR_RESET}\n")
        except Exception as e:
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_RED}Fatal Error: {e}{COLOR_RESET}\n")
        finally:
            self.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="M300 & SPD3303X Battery Balancer")
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Force simulation mode without connecting to hardware",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=3.60,
        help="Target balance voltage in Volts (default: 3.60)",
    )
    parser.add_argument(
        "--delta",
        type=float,
        default=0.02,
        help="Balancing delta threshold in Volts (default: 0.02)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Measurement cycle interval in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--mode",
        choices=["single", "parallel"],
        default="single",
        help="Balancing mode: single cell charging (default, safest) or parallel charging",
    )
    parser.add_argument(
        "--parallel-delta",
        type=float,
        default=0.01,
        help="Maximum voltage difference in Volts between cells allowed to charge in parallel (default: 0.01)",
    )
    parser.add_argument(
        "--max-voltage",
        type=float,
        default=3.65,
        help="Maximum safety cell voltage limit in Volts (default: 3.65)",
    )
    parser.add_argument(
        "--max-current",
        type=float,
        default=3.2,
        help="Maximum safety PSU channel current limit in Amperes (default: 3.2)",
    )
    parser.add_argument(
        "--max-cell-current",
        type=float,
        default=1.2,
        help="Maximum safety individual cell current limit in Amperes (default: 1.2)",
    )
    parser.add_argument(
        "--batches",
        choices=["1", "2", "both"],
        default="both",
        help="Select which cell batch to balance: 1 (bottom 8), 2 (top 8), or both (default)",
    )

    args = parser.parse_args()

    app = BalancerApp(
        target_voltage=args.target,
        balance_delta=args.delta,
        parallel_delta=args.parallel_delta,
        simulated=args.sim,
        interval=args.interval,
        mode=args.mode,
        max_voltage=args.max_voltage,
        max_current=args.max_current,
        max_cell_current=args.max_cell_current,
        batches=args.batches,
    )
    app.run()


if __name__ == "__main__":
    main()
