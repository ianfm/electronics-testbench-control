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
    voltage_reading: float = 0.0
    last_voltages: List[float] = None
    completed_cells: List[int] = None

    def __post_init__(self):
        if self.active_cells is None:
            self.active_cells = []
        if self.last_voltages is None:
            self.last_voltages = [0.0] * len(self.mux_channels)
        if self.completed_cells is None:
            self.completed_cells = []


class BalancerApp:
    def __init__(
        self,
        target_voltage: float = 3.50,
        balance_delta: float = 0.02,
        parallel_delta: float = 0.01,
        simulated: bool = False,
        interval: float = 5.0,
        mode: str = "single",
        max_voltage: float = 3.65,
        max_current: float = 3.2,
        max_cell_current: float = 1.2,
        batches: str = "both",
        ocv_interval: float = 60.0,
        monitor: bool = False,
        headroom: float = 0.15,
        termination_current: float = 0.05,
        log_dir: str = "logs",
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
        self.ocv_interval = ocv_interval
        self.monitor = monitor
        self.headroom = headroom
        self.termination_current = termination_current
        self.log_dir = log_dir
        self.last_ocv_time = 0.0
        self.trigger_ocv_next = True  # Start with an OCV measurement
        self.psu_overcurrent_cycles = {1: 0, 2: 0}
        self.cell_overcurrent_cycles = [0] * 16
        self.connection_error = None
        self.log_file = None
        self.log_writer = None
        self.cell_ah = [0.0] * 16
        self.cell_ir = [0.0] * 16
        self.last_step_time = 0.0
        self.pre_ocv_voltages = None
        self.pre_ocv_currents = None

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
        self.connection_error = None
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
                if self.monitor:
                    print("Siglent SPD3303X PSU not found; continuing in monitor-only mode with DAQ only.")
                    self.psu = None
                else:
                    raise RuntimeError("Siglent SPD3303X PSU not found on the bench.")
            else:
                self.psu = bind_driver("siglent-spd3303x", psu_device)

            print(f"Connected to DAQ: {self.daq.identify().strip()}")
            if self.psu:
                print(f"Connected to PSU: {self.psu.identify().strip()}")

            # Make sure DMM is enabled
            if not self.daq.dmm_installed() or not self.daq.dmm_enabled():
                raise RuntimeError("M300 DMM card is not installed or enabled.")

            # In monitor-only mode, ensure PSU outputs are off and relays are open initially
            if self.monitor:
                if self.psu:
                    self.psu.output_off(1)
                    self.psu.output_off(2)
                self.daq.open_channels(range(301, 317))
                self.daq.clear_scan()

        except Exception as e:
            self.connection_error = str(e)
            print(f"{COLOR_RED}Error connecting to hardware: {e}{COLOR_RESET}")
            print("Defaulting to SIMULATION mode.")
            self.simulated = True
            time.sleep(2.0)

    def shutdown(self) -> None:
        """Safely open all gates and turn off the PSU channels."""
        print("\nInitiating safety shutdown...")
        self.close_logging()
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

    def measure_cell_voltages(self, batch: BatchState, open_circuit: bool = True) -> List[float]:
        """Measure cell voltages in a batch, optionally opening relays to measure OCV."""
        if self.simulated:
            # Return simulated voltages for this batch
            if batch.psu_channel == 1:
                indices = range(0, len(batch.mux_channels))
            else:
                n1 = len(self.batch1.mux_channels)
                indices = range(n1, n1 + len(batch.mux_channels))
            return [self.sim_voltages[i] for i in indices]

        if open_circuit:
            # Cold switching: disable PSU output before opening relays
            if self.psu:
                self.psu.output_off(batch.psu_channel)
                time.sleep(0.1)  # wait 100ms for current to decay

            # 1. Open charge relays for this batch to ensure we measure open-circuit/settled voltage
            self.daq.open_channels(batch.act_channels)
            time.sleep(1.0)  # Wait 1 second for relay contact settling & battery chemistry settling

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
        offset = 0 if batch.psu_channel == 1 else len(self.batch1.mux_channels)
        
        # Sync any cell that resting OCV is already >= target to completed
        # and de-complete cells if they relax below target - balance_delta, capped at 3.28V
        recharge_threshold = min(self.target_voltage - self.balance_delta, 3.28)
        for idx, v in enumerate(voltages):
            global_cell_num = idx + 1 + offset
            if v >= self.target_voltage:
                if global_cell_num not in batch.completed_cells:
                    batch.completed_cells.append(global_cell_num)
            else:
                if global_cell_num in batch.completed_cells and v < recharge_threshold:
                    batch.completed_cells.remove(global_cell_num)

        # Filter cells that are not completed yet
        non_completed_indices = [
            i for i in range(len(batch.mux_channels))
            if (i + 1 + offset) not in batch.completed_cells
        ]

        if not non_completed_indices:
            batch.status = "COMPLETED"
            batch.charge_mode = "OFF"
            batch.active_cells = []
            batch.voltage_set = 0.0
            batch.current_set = 0.0
            batch.current_reading = 0.0
            batch.voltage_reading = 0.0
            if not self.simulated:
                self.psu.output_off(batch.psu_channel)
                self.daq.open_channels(batch.act_channels)
            return

        # Find weakest cell among non-completed
        v_min = min(voltages[i] for i in non_completed_indices)

        # Select cells to charge:
        # If running in "single" mode, only charge the single weakest cell.
        # Otherwise, in "parallel" mode, charge all cells within parallel_delta of the weakest cell.
        active_indices = []
        if self.mode == "single":
            if v_min < self.target_voltage:
                weakest_idx = voltages.index(v_min)
                active_indices = [weakest_idx]
        else:
            for idx in non_completed_indices:
                v = voltages[idx]
                if v < self.target_voltage and v < (v_min + self.parallel_delta):
                    active_indices.append(idx)

        batch.active_cells = [idx + 1 + offset for idx in active_indices]

        if not batch.active_cells:
            # If no cells selected, default to the single weakest cell to continue absorption
            weakest_idx = voltages.index(v_min)
            batch.active_cells = [weakest_idx + 1 + offset]
            active_indices = [weakest_idx]

        # Determine charge rates based on weakest cell voltage
        if v_min < 3.0:
            batch.charge_mode = "RECOVERY"
            batch.voltage_set = min(3.30, self.target_voltage - 0.2)
            i_cell = 0.2  # Gentle recovery current
        elif v_min < 3.35:
            batch.charge_mode = "BULK"
            batch.voltage_set = min(self.target_voltage + self.headroom, self.max_voltage - 0.01)
            i_cell = max(0.1, self.max_cell_current - 0.2)  # Bulk charge targets max_cell_current - 0.2 to leave margin for noise
        else:
            batch.charge_mode = "ABSORPTION"
            batch.voltage_set = min(self.target_voltage + self.headroom, self.max_voltage - 0.01)
            i_cell = min(0.3, self.max_cell_current)  # Precision top-off/balancing current

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
            
            # If the calculated current limit is too low, fall back to single cell charging
            if i_total_limit < 0.3:
                weakest_idx = voltages.index(v_min)
                active_indices = [weakest_idx]
                batch.active_cells = [weakest_idx + 1 + offset]
                batch.current_set = min(self.max_cell_current, i_cell)
            else:
                batch.current_set = min(batch.current_set, i_total_limit)

        batch.status = "CHARGING"

        # Apply settings to hardware or simulate
        if self.simulated:
            # Physical simulation of current reading (handles CC and CV modes)
            R_total = 0.15  # Ohms (wire drop + cell internal resistance)
            calculated_total_i = sum(max(0.0, (batch.voltage_set - self.sim_voltages[idx + offset]) / R_total) for idx in active_indices)
            batch.current_reading = min(batch.current_set, calculated_total_i) * random.uniform(0.98, 1.02)
            
            # Simulated PSU terminal voltage readback
            v_avg = sum(self.sim_voltages[idx + offset] for idx in active_indices) / len(active_indices) if active_indices else 3.2
            batch.voltage_reading = min(batch.voltage_set, v_avg + batch.current_reading * 0.15) * random.uniform(0.99, 1.01)
        else:
            # Configure PSU settings
            self.psu.set_voltage(batch.psu_channel, batch.voltage_set)
            self.psu.set_current(batch.psu_channel, batch.current_set)

            # Map active indices to actual ACT channels
            active_acts = [batch.act_channels[idx] for idx in active_indices]
            inactive_acts = [ch for ch in batch.act_channels if ch not in active_acts]

            # Cold switching: change relay states while PSU output is off
            self.daq.open_channels(inactive_acts)
            self.daq.close_channels(active_acts)
            time.sleep(0.1)  # Wait 100ms for contacts to physically settle

            # Turn PSU output ON after relays are settled
            self.psu.output_on(batch.psu_channel)
            time.sleep(0.2)  # Wait 200ms for PSU output loops and ADC to stabilize

            # Get actual telemetry readbacks
            curr = self.psu.get_current(batch.psu_channel)
            batch.current_reading = curr if curr is not None else 0.0
            volt = self.psu.get_voltage(batch.psu_channel)
            batch.voltage_reading = volt if volt is not None else 0.0

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
        
        if self.simulated:
            sim_status = f"{COLOR_BOLD}{COLOR_RED}[SIMULATION]{COLOR_RESET}"
        else:
            sim_status = f"{COLOR_BOLD}{COLOR_GREEN}[HARDWARE]{COLOR_RESET}"

        # Helper to construct exactly padded lines for the boxed header
        import re
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        def make_line(left: str, right: str = "", width: int = 76) -> str:
            raw_left = ansi_escape.sub('', left)
            raw_right = ansi_escape.sub('', right)
            space_len = width - len(raw_left) - len(raw_right)
            if space_len < 0:
                space_len = 0
            return f"│ {left}{' ' * space_len}{right} │\n"

        # 1. Main Header Box
        sys.stdout.write("┌" + "─" * 81 + "┐\n")
        sys.stdout.write(make_line(
            f"{COLOR_BOLD}{COLOR_BLUE}LiFePO4 BATTERY CELL 16-CHANNEL BALANCER{COLOR_RESET}",
            sim_status,
            width=79
        ))
        sys.stdout.write("├" + "─" * 81 + "┤\n")
        
        recharge_th = min(self.target_voltage - self.balance_delta, 3.28)
        sys.stdout.write(make_line(
            f"Target Voltage: {COLOR_BOLD}{self.target_voltage:.3f} V{COLOR_RESET}  │  Balance Delta: {COLOR_BOLD}{self.balance_delta*1000:.0f} mV{COLOR_RESET}  │  Parallel Delta: {COLOR_BOLD}{self.parallel_delta*1000:.0f} mV{COLOR_RESET}",
            width=79
        ))
        sys.stdout.write(make_line(
            f"Voltage Headroom: {COLOR_BOLD}{self.headroom:.2f} V{COLOR_RESET}  │  Termination I: {COLOR_BOLD}{self.termination_current:.3f} A{COLOR_RESET}  │  OCV Interval: {COLOR_BOLD}{self.ocv_interval:.1f} s{COLOR_RESET}",
            width=79
        ))
        sys.stdout.write(make_line(
            f"Recharge OCV: {COLOR_BOLD}{recharge_th:.3f} V{COLOR_RESET}  │  Absorption Trig: {COLOR_BOLD}3.350 V{COLOR_RESET}",
            width=79
        ))
        sys.stdout.write("└" + "─" * 81 + "┘\n\n")

        # Helper to format column values safely (accounting for ANSI wrapping)
        def format_col(text: str, width: int, color_prefix: str = "", alignment: str = "^") -> str:
            clean = ansi_escape.sub('', text)
            visible_len = len(clean)
            padding_needed = max(0, width - visible_len)
            
            if alignment == "^":
                left = padding_needed // 2
                right = padding_needed - left
                padded = " " * left + text + " " * right
            elif alignment == "<":
                padded = text + " " * padding_needed
            else:
                padded = " " * padding_needed + text
            
            if color_prefix:
                return f"{color_prefix}{padded}{COLOR_RESET}"
            return padded

        # Helper to build table rows
        def make_row(cell: str, voltage: str, progress: str, capacity: str, ir: str, status: str,
                     cell_color="", voltage_color="", progress_color="", capacity_color="", ir_color="", status_color="") -> str:
            c_cell = format_col(cell, 6, cell_color)
            c_volt = format_col(voltage, 12, voltage_color)
            c_prog = format_col(progress, 20, progress_color, alignment="<")
            c_cap = format_col(capacity, 14, capacity_color)
            c_ir = format_col(ir, 12, ir_color)
            c_stat = format_col(status, 12, status_color)
            return f"│{c_cell}│{c_volt}│{c_prog}│{c_cap}│{c_ir}│{c_stat}│\n"

        # Helper to draw cell progress bar
        def get_bar(v: float) -> Tuple[str, str]:
            min_v, max_v = 2.5, self.target_voltage
            pct = max(0.0, min(1.0, (v - min_v) / (max_v - min_v)))
            filled = int(pct * 12)
            bar_str = "█" * filled + "░" * (12 - filled)
            bar_text = f"[{bar_str}] {pct*100:3.0f}%"
            color = COLOR_RED if v < 3.0 else COLOR_YELLOW if v < 3.45 else COLOR_GREEN
            return bar_text, color

        # Helper to draw a colored VU meter bar
        def make_vu_bar(val: float, max_val: float, length: int = 20) -> str:
            pct = max(0.0, min(1.0, val / max_val)) if max_val > 0.0 else 0.0
            filled = int(pct * length)
            bar_parts = []
            for i in range(length):
                if i < filled:
                    pos_pct = i / length
                    if pos_pct < 0.70:
                        bar_parts.append(f"{COLOR_GREEN}█{COLOR_RESET}")
                    elif pos_pct < 0.90:
                        bar_parts.append(f"{COLOR_YELLOW}█{COLOR_RESET}")
                    else:
                        bar_parts.append(f"{COLOR_RED}█{COLOR_RESET}")
                else:
                    bar_parts.append("\033[90m░\033[0m")
            return "[" + "".join(bar_parts) + "]"

        # Helper to build a batch divider
        def make_batch_section(batch: BatchState) -> str:
            color = COLOR_GREEN if batch.status == "COMPLETED" else COLOR_CYAN if batch.status == "CHARGING" else COLOR_YELLOW
            title = f"  {batch.name.upper()}  ──  STATUS: {batch.status}  │  MODE: {batch.charge_mode}"
            
            if batch.status == "CHARGING":
                # Determine mode: CC if current is close to limit, CV if voltage is close to setpoint
                is_cc = batch.current_reading >= batch.current_set * 0.97
                is_cv = batch.voltage_reading >= batch.voltage_set * 0.99
                
                # If both or neither trigger, default to CC if current is closer to limit than voltage is to setpoint
                v_pct = batch.voltage_reading / batch.voltage_set if batch.voltage_set > 0 else 0.0
                i_pct = batch.current_reading / batch.current_set if batch.current_set > 0 else 0.0
                if (is_cc and is_cv) or (not is_cc and not is_cv):
                    if i_pct >= v_pct:
                        is_cc, is_cv = True, False
                    else:
                        is_cc, is_cv = False, True
                
                if is_cc:
                    vu_v = make_vu_bar(batch.voltage_reading, batch.voltage_set, length=20)
                    vu_i = make_vu_bar(batch.current_set, batch.current_set, length=20)
                    mode_v = ""
                    mode_i = f" {COLOR_BOLD}{COLOR_RED}[CC]{COLOR_RESET}"
                    i_display = batch.current_set
                    v_display = batch.voltage_reading
                else:
                    vu_v = make_vu_bar(batch.voltage_set, batch.voltage_set, length=20)
                    vu_i = make_vu_bar(batch.current_reading, batch.current_set, length=20)
                    mode_v = f" {COLOR_BOLD}{COLOR_GREEN}[CV]{COLOR_RESET}"
                    mode_i = ""
                    i_display = batch.current_reading
                    v_display = batch.voltage_set

                subtitle1 = f"  PSU V: {vu_v} {v_display:.3f} V  (Setpoint: {batch.voltage_set:.3f} V){mode_v}"
                subtitle2 = f"  PSU I: {vu_i} {i_display:.3f} A  (Limit:    {batch.current_set:.3f} A){mode_i}"
            else:
                vu_v = make_vu_bar(0.0, max(0.1, batch.voltage_set), length=20)
                vu_i = make_vu_bar(0.0, max(0.01, batch.current_set), length=20)
                subtitle1 = f"  PSU V: {vu_v} OFF      (Setpoint: {batch.voltage_set:.3f} V)"
                subtitle2 = f"  PSU I: {vu_i} OFF      (Limit:    {batch.current_set:.3f} A)"
            
            line1 = format_col(title, 81, color + COLOR_BOLD, alignment="<")
            line2 = format_col(subtitle1, 81, COLOR_RESET, alignment="<")
            line3 = format_col(subtitle2, 81, COLOR_RESET, alignment="<")
            
            out = "├" + "─" * 81 + "┤\n"
            out += f"│{line1}│\n"
            out += f"│{line2}│\n"
            out += f"│{line3}│\n"
            out += "├" + "─" * 81 + "┤\n"
            return out

        # Start printing the Cell Matrix Table
        sys.stdout.write("┌" + "─" * 6 + "┬" + "─" * 12 + "┬" + "─" * 20 + "┬" + "─" * 14 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┐\n")
        
        # Headers
        c_cell = format_col("Cell", 6, COLOR_BOLD)
        c_volt = format_col("Voltage", 12, COLOR_BOLD)
        c_prog = format_col("Progress Bar", 20, COLOR_BOLD)
        c_cap = format_col("Capacity", 14, COLOR_BOLD)
        c_ir = format_col("DC IR", 12, COLOR_BOLD)
        c_stat = format_col("Status", 12, COLOR_BOLD)
        sys.stdout.write(f"│{c_cell}│{c_volt}│{c_prog}│{c_cap}│{c_ir}│{c_stat}│\n")

        # Batch 1 section
        sys.stdout.write(make_batch_section(self.batch1))
        for i in range(len(self.batch1.mux_channels)):
            cell_idx = i
            cell_num = cell_idx + 1
            v = voltages[cell_idx]
            bar_text, bar_color = get_bar(v)
            
            # Determine cell status and color
            if cell_num in (self.batch1.completed_cells or []):
                stat = "COMPLETED"
                stat_color = COLOR_GREEN
            elif cell_num in (self.batch1.active_cells or []):
                stat = "CHARGING"
                stat_color = COLOR_YELLOW
            else:
                stat = "IDLE"
                stat_color = ""

            ir_text = f"{self.cell_ir[cell_idx] * 1000:.1f} mΩ" if self.cell_ir[cell_idx] > 0.0 else "-- mΩ"
            ah_text = f"{self.cell_ah[cell_idx]:.3f} Ah"

            sys.stdout.write(make_row(
                f"{cell_num:02d}",
                f"{v:.4f} V",
                bar_text,
                ah_text,
                ir_text,
                stat,
                progress_color=bar_color,
                status_color=stat_color
            ))

        # Batch 2 section
        sys.stdout.write(make_batch_section(self.batch2))
        offset = len(self.batch1.mux_channels)
        for i in range(len(self.batch2.mux_channels)):
            cell_idx = offset + i
            cell_num = cell_idx + 1
            v = voltages[cell_idx]
            bar_text, bar_color = get_bar(v)
            
            # Determine cell status and color
            if cell_num in (self.batch2.completed_cells or []):
                stat = "COMPLETED"
                stat_color = COLOR_GREEN
            elif cell_num in (self.batch2.active_cells or []):
                stat = "CHARGING"
                stat_color = COLOR_YELLOW
            else:
                stat = "IDLE"
                stat_color = ""

            ir_text = f"{self.cell_ir[cell_idx] * 1000:.1f} mΩ" if self.cell_ir[cell_idx] > 0.0 else "-- mΩ"
            ah_text = f"{self.cell_ah[cell_idx]:.3f} Ah"

            sys.stdout.write(make_row(
                f"{cell_num:02d}",
                f"{v:.4f} V",
                bar_text,
                ah_text,
                ir_text,
                stat,
                progress_color=bar_color,
                status_color=stat_color
            ))

        # Bottom Border of Table
        sys.stdout.write("└" + "─" * 6 + "┴" + "─" * 12 + "┴" + "─" * 20 + "┴" + "─" * 14 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┘\n")
        sys.stdout.write(f"  {COLOR_BOLD}{COLOR_YELLOW}Press Ctrl+C to abort charging and safely open all relays.{COLOR_RESET}\n")

    def step(self) -> bool:
        """Executes a single monitoring, control, and safety check step.

        Returns:
            True if charging/balancing is completed for both batches, False otherwise.
        """
        if self.monitor:
            # Monitor-only mode: measure voltages directly with open_circuit=True to use the verified hardware pathway
            v1 = self.measure_cell_voltages(self.batch1, open_circuit=True) if self.batch1.status != "COMPLETED" else [0.0] * len(self.batch1.mux_channels)
            v2 = self.measure_cell_voltages(self.batch2, open_circuit=True) if self.batch2.status != "COMPLETED" else [0.0] * len(self.batch2.mux_channels)
            all_voltages = v1 + v2
            
            # Keep batches in monitoring/off state
            for batch in (self.batch1, self.batch2):
                if batch.status != "COMPLETED":
                    batch.status = "MONITORING"
                    batch.charge_mode = "OFF"
                    batch.active_cells = []
                    batch.current_reading = 0.0
                    batch.voltage_reading = 0.0

            # Perform safety checks
            n1 = len(self.batch1.mux_channels)
            for idx, v in enumerate(all_voltages):
                batch_completed = self.batch1.status == "COMPLETED" if idx < n1 else self.batch2.status == "COMPLETED"
                if not batch_completed:
                    if v > self.max_voltage:
                        raise ValueError(
                            f"CRITICAL SAFETY VIOLATION: Cell {idx + 1} voltage {v:.3f} V "
                            f"exceeded maximum safety limit of {self.max_voltage:.3f} V! "
                            f"Aborting immediately."
                        )
                    if v < 0.0:
                        raise ValueError(
                            f"CRITICAL SAFETY VIOLATION: Cell {idx + 1} voltage {v:.3f} V "
                            f"is negative! Aborting immediately."
                        )

            # Print dashboard and return False
            self.print_dashboard(all_voltages)
            return False

        current_time = time.time()

        # Integrate charge (Ah capacity delivery)
        if getattr(self, "last_step_time", 0.0) > 0.0:
            dt = current_time - self.last_step_time
            if dt < 60.0:
                n1 = len(self.batch1.mux_channels)
                if self.batch1.status == "CHARGING" and self.batch1.active_cells:
                    local_active1 = [c for c in self.batch1.active_cells]
                    c1_last = self.calculate_cell_currents(self.batch1.last_voltages, local_active1, self.batch1.current_reading)
                else:
                    c1_last = [0.0] * len(self.batch1.mux_channels)

                if self.batch2.status == "CHARGING" and self.batch2.active_cells:
                    local_active2 = [c - n1 for c in self.batch2.active_cells]
                    c2_last = self.calculate_cell_currents(self.batch2.last_voltages, local_active2, self.batch2.current_reading)
                else:
                    c2_last = [0.0] * len(self.batch2.mux_channels)
                
                last_currents = c1_last + c2_last
                for idx in range(len(last_currents)):
                    self.cell_ah[idx] += (last_currents[idx] * dt) / 3600.0
                    
        self.last_step_time = current_time

        need_ocv = (
            self.trigger_ocv_next or
            self.last_ocv_time == 0.0 or
            (current_time - self.last_ocv_time) >= self.ocv_interval
        )

        if need_ocv:
            # We are about to run an OCV measurement. Save the pre-OCV load state
            # for any batch that was charging on the previous step.
            self.pre_ocv_voltages = list(self.batch1.last_voltages + self.batch2.last_voltages)
            
            # Reconstruct the last step's currents for all cells
            n1 = len(self.batch1.mux_channels)
            if self.batch1.status == "CHARGING" and self.batch1.active_cells:
                local_active1 = [c for c in self.batch1.active_cells]
                c1_last = self.calculate_cell_currents(self.batch1.last_voltages, local_active1, self.batch1.current_reading)
            else:
                c1_last = [0.0] * len(self.batch1.mux_channels)

            if self.batch2.status == "CHARGING" and self.batch2.active_cells:
                local_active2 = [c - n1 for c in self.batch2.active_cells]
                c2_last = self.calculate_cell_currents(self.batch2.last_voltages, local_active2, self.batch2.current_reading)
            else:
                c2_last = [0.0] * len(self.batch2.mux_channels)
            self.pre_ocv_currents = c1_last + c2_last

        # 1. Measure cell voltages
        if self.batch1.status != "COMPLETED":
            v1 = self.measure_cell_voltages(self.batch1, open_circuit=need_ocv)
            self.batch1.last_voltages = v1
        else:
            v1 = self.batch1.last_voltages

        if self.batch2.status != "COMPLETED":
            v2 = self.measure_cell_voltages(self.batch2, open_circuit=need_ocv)
            self.batch2.last_voltages = v2
        else:
            v2 = self.batch2.last_voltages

        all_voltages = v1 + v2

        if need_ocv:
            self.trigger_ocv_next = False
            self.last_ocv_time = current_time
            
            # Calculate DC IR using the step-response delta
            if getattr(self, "pre_ocv_voltages", None) and getattr(self, "pre_ocv_currents", None):
                num_cells = min(16, len(self.pre_ocv_voltages))
                for idx in range(num_cells):
                    v_load = self.pre_ocv_voltages[idx]
                    v_ocv = all_voltages[idx]
                    i_load = self.pre_ocv_currents[idx]
                    if i_load > 0.1:
                        r_int = (v_load - v_ocv) / i_load
                        if 0.0 < r_int < 2.0:
                            if self.cell_ir[idx] == 0.0:
                                self.cell_ir[idx] = r_int
                            else:
                                self.cell_ir[idx] = 0.7 * self.cell_ir[idx] + 0.3 * r_int

        # Safety Check: Individual cell overvoltage and reverse polarity protection
        n1 = len(self.batch1.mux_channels)
        for idx, v in enumerate(all_voltages):
            batch_completed = self.batch1.status == "COMPLETED" if idx < n1 else self.batch2.status == "COMPLETED"
            if not batch_completed:
                if v > self.max_voltage:
                    raise ValueError(
                        f"CRITICAL SAFETY VIOLATION: Cell {idx + 1} voltage {v:.3f} V "
                        f"exceeded maximum safety limit of {self.max_voltage:.3f} V! "
                        f"Aborting immediately."
                    )
                if v < 0.0:
                    raise ValueError(
                        f"CRITICAL SAFETY VIOLATION: Cell {idx + 1} voltage {v:.3f} V "
                        f"is negative! This indicates a reverse polarity wiring error or a severely damaged cell. "
                        f"Aborting immediately."
                    )

        # 2. Update state and outputs
        if need_ocv:
            # Update state machine and configuration only with fresh OCV measurements
            if self.batch1.status != "COMPLETED":
                self.update_batch_state(self.batch1, v1)
            if self.batch2.status != "COMPLETED":
                self.update_batch_state(self.batch2, v2)
        else:
            # During under-charge steps, we keep relays in place, but update current readbacks
            for batch in (self.batch1, self.batch2):
                if batch.status == "CHARGING":
                    if self.simulated:
                        R_total = 0.15  # Ohms (wire drop + cell internal resistance)
                        offset = 0 if batch.psu_channel == 1 else len(self.batch1.mux_channels)
                        active_indices = [c - 1 - offset for c in batch.active_cells]
                        calculated_total_i = sum(max(0.0, (batch.voltage_set - self.sim_voltages[offset + idx]) / R_total) for idx in active_indices)
                        batch.current_reading = min(batch.current_set, calculated_total_i) * random.uniform(0.98, 1.02)
                        
                        # Simulated PSU terminal voltage readback
                        v_avg = sum(self.sim_voltages[offset + idx] for idx in active_indices) / len(active_indices) if active_indices else 3.2
                        batch.voltage_reading = min(batch.voltage_set, v_avg + batch.current_reading * 0.15) * random.uniform(0.99, 1.01)
                    else:
                        curr = self.psu.get_current(batch.psu_channel)
                        batch.current_reading = curr if curr is not None else 0.0
                        volt = self.psu.get_voltage(batch.psu_channel)
                        batch.voltage_reading = volt if volt is not None else 0.0

            # Proportional voltage setpoint headroom and feedback regulation, and check active cell CV termination
            for batch, voltages_batch in [(self.batch1, v1), (self.batch2, v2)]:
                if batch.status == "CHARGING" and batch.active_cells:
                    offset = 0 if batch.psu_channel == 1 else len(self.batch1.mux_channels)
                    local_active = [c - offset for c in batch.active_cells]
                    currents = self.calculate_cell_currents(voltages_batch, local_active, batch.current_reading)
                    
                    # 1. Proportional voltage setpoint headroom & active feedback regulation
                    if batch.charge_mode in ("BULK", "ABSORPTION"):
                        # Feedforward: reduce PSU voltage as total current decays
                        if batch.current_set > 0.0:
                            ratio = min(1.0, max(0.0, batch.current_reading / batch.current_set))
                        else:
                            ratio = 1.0
                        v_set = self.target_voltage + self.headroom * ratio
                        
                        # Feedback: if any active cell's terminal voltage exceeds target, trim setpoint to prevent overshoot
                        active_voltages = [voltages_batch[c - 1 - offset] for c in batch.active_cells]
                        if active_voltages:
                            v_active_max = max(active_voltages)
                            if v_active_max > self.target_voltage:
                                overshoot = v_active_max - self.target_voltage
                                v_set = max(self.target_voltage, v_set - overshoot)
                        
                        batch.voltage_set = min(v_set, self.max_voltage - 0.01)
                        if not self.simulated:
                            self.psu.set_voltage(batch.psu_channel, batch.voltage_set)
                    
                    # 2. Check for active cell CV absorption completion
                    for idx, cell_voltage in enumerate(voltages_batch):
                        global_cell_num = idx + 1 + offset
                        
                        if global_cell_num in batch.active_cells:
                            local_idx = global_cell_num - 1 - offset
                            cell_current = currents[local_idx]
                            # CV Absorption termination criteria:
                            # Terminal voltage reaches target-10mV, AND cell current decays below termination threshold
                            if cell_voltage >= (self.target_voltage - 0.01) and cell_current <= self.termination_current:
                                if global_cell_num not in batch.completed_cells:
                                    batch.completed_cells.append(global_cell_num)
                                    self.trigger_ocv_next = True

        # Safety Check: PSU channel total overcurrent protection (debounced)
        for batch in (self.batch1, self.batch2):
            if batch.status != "COMPLETED":
                if batch.current_reading > self.max_current:
                    self.psu_overcurrent_cycles[batch.psu_channel] += 1
                    if self.psu_overcurrent_cycles[batch.psu_channel] >= 2:
                        raise ValueError(
                            f"CRITICAL SAFETY VIOLATION: PSU Channel {batch.psu_channel} current {batch.current_reading:.3f} A "
                            f"exceeded safety limit of {self.max_current:.3f} A for 2 consecutive cycles! "
                            f"Aborting immediately."
                        )
                else:
                    self.psu_overcurrent_cycles[batch.psu_channel] = 0

        # Safety Check: Calculated individual cell overcurrent protection (debounced & throttled)
        if self.batch1.status != "COMPLETED":
            currents1 = self.calculate_cell_currents(
                v1, self.batch1.active_cells, self.batch1.current_reading
            )
            n1 = len(self.batch1.mux_channels)
            active_idxs1 = [c - 1 for c in self.batch1.active_cells]
            max_calc_i1 = max(currents1[idx] for idx in active_idxs1) if active_idxs1 else 0.0

            if max_calc_i1 > self.max_cell_current:
                if self.batch1.current_set > 0.15 and not self.simulated:
                    throttle_factor = self.max_cell_current / max_calc_i1
                    new_limit = max(0.1, self.batch1.current_set * throttle_factor * 0.9)
                    self.batch1.current_set = new_limit
                    self.psu.set_current(self.batch1.psu_channel, new_limit)
                    self.cell_overcurrent_cycles[0:n1] = [0] * n1
                else:
                    for idx in range(len(v1)):
                        if currents1[idx] > self.max_cell_current:
                            self.cell_overcurrent_cycles[idx] += 1
                            if self.cell_overcurrent_cycles[idx] >= 2:
                                raise ValueError(
                                    f"CRITICAL SAFETY VIOLATION: Calculated current for Cell {idx + 1} is {currents1[idx]:.3f} A, "
                                    f"exceeded individual cell current safety limit of {self.max_cell_current:.3f} A for 2 consecutive cycles! "
                                    f"Aborting immediately."
                                )
                        else:
                            self.cell_overcurrent_cycles[idx] = 0
            else:
                self.cell_overcurrent_cycles[0:n1] = [0] * n1

        if self.batch2.status != "COMPLETED":
            n1 = len(self.batch1.mux_channels)
            currents2 = self.calculate_cell_currents(
                v2, [c - n1 for c in self.batch2.active_cells], self.batch2.current_reading
            )
            active_idxs2 = [c - n1 - 1 for c in self.batch2.active_cells]
            max_calc_i2 = max(currents2[idx] for idx in active_idxs2) if active_idxs2 else 0.0

            if max_calc_i2 > self.max_cell_current:
                if self.batch2.current_set > 0.15 and not self.simulated:
                    throttle_factor = self.max_cell_current / max_calc_i2
                    new_limit = max(0.1, self.batch2.current_set * throttle_factor * 0.9)
                    self.batch2.current_set = new_limit
                    self.psu.set_current(self.batch2.psu_channel, new_limit)
                    self.cell_overcurrent_cycles[n1:n1+len(v2)] = [0] * len(v2)
                else:
                    for idx in range(len(v2)):
                        global_idx = idx + n1
                        if currents2[idx] > self.max_cell_current:
                            self.cell_overcurrent_cycles[global_idx] += 1
                            if self.cell_overcurrent_cycles[global_idx] >= 2:
                                raise ValueError(
                                    f"CRITICAL SAFETY VIOLATION: Calculated current for Cell {global_idx + 1} is {currents2[idx]:.3f} A, "
                                    f"exceeded individual cell current safety limit of {self.max_cell_current:.3f} A for 2 consecutive cycles! "
                                    f"Aborting immediately."
                                )
                        else:
                            self.cell_overcurrent_cycles[global_idx] = 0
            else:
                self.cell_overcurrent_cycles[n1:n1+len(v2)] = [0] * len(v2)

        # 3. Write log entry
        n1 = len(self.batch1.mux_channels)
        if self.batch1.status == "CHARGING" and self.batch1.active_cells:
            local_active1 = [c for c in self.batch1.active_cells]
            c1 = self.calculate_cell_currents(v1, local_active1, self.batch1.current_reading)
        else:
            c1 = [0.0] * len(v1)

        if self.batch2.status == "CHARGING" and self.batch2.active_cells:
            local_active2 = [c - n1 for c in self.batch2.active_cells]
            c2 = self.calculate_cell_currents(v2, local_active2, self.batch2.current_reading)
        else:
            c2 = [0.0] * len(v2)
        self.write_log_entry(all_voltages, c1 + c2)

        # 4. Render dashboard
        self.print_dashboard(all_voltages)

        if self.batch1.status == "COMPLETED" and self.batch2.status == "COMPLETED":
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_GREEN}Balancing complete! All cells charged successfully.{COLOR_RESET}\n")
            return True

        return False

    def init_logging(self) -> None:
        import os
        import csv
        if not self.log_dir:
            return
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            self.log_path = os.path.join(self.log_dir, f"balancer_log_{timestamp}.csv")
            self.log_file = open(self.log_path, "w", newline="")
            self.log_writer = csv.writer(self.log_file)
            
            # Write header
            headers = [
                "timestamp",
                "b1_status", "b1_charge_mode", "b1_psu_v", "b1_psu_i",
                "b2_status", "b2_charge_mode", "b2_psu_v", "b2_psu_i"
            ]
            for c in range(1, 17):
                headers.append(f"v_cell_{c:02d}")
            for c in range(1, 17):
                headers.append(f"i_cell_{c:02d}")
            for c in range(1, 17):
                headers.append(f"ir_cell_{c:02d}")
            for c in range(1, 17):
                headers.append(f"ah_cell_{c:02d}")
            self.log_writer.writerow(headers)
            self.log_file.flush()
            print(f"Logging telemetry to: {self.log_path}")
        except Exception as e:
            print(f"Failed to initialize log file: {e}")
            self.log_file = None
            self.log_writer = None

    def close_logging(self) -> None:
        if getattr(self, "log_file", None):
            try:
                self.log_file.close()
                print(f"Closed log file: {self.log_path}")
            except Exception as e:
                pass
            self.log_file = None
            self.log_writer = None

    def write_log_entry(self, voltages: List[float], currents: List[float]) -> None:
        if not getattr(self, "log_file", None):
            return
        import datetime
        timestamp_str = datetime.datetime.now().isoformat(timespec='milliseconds')
        row = [
            timestamp_str,
            self.batch1.status, self.batch1.charge_mode, self.batch1.voltage_reading, self.batch1.current_reading,
            self.batch2.status, self.batch2.charge_mode, self.batch2.voltage_reading, self.batch2.current_reading,
        ]
        # Pad voltages and currents to 16 elements to match the fixed 73-column CSV header format
        padded_voltages = voltages + [0.0] * (16 - len(voltages))
        padded_currents = currents + [0.0] * (16 - len(currents))

        for v in padded_voltages:
            row.append(f"{v:.4f}")
        for i in padded_currents:
            row.append(f"{i:.3f}")
        for r in self.cell_ir:
            row.append(f"{r:.4f}")
        for ah in self.cell_ah:
            row.append(f"{ah:.4f}")
        
        try:
            self.log_writer.writerow(row)
            self.log_file.flush()
        except Exception as e:
            pass

    def run(self) -> None:
        """Main application loop."""
        self.connect()
        self.init_logging()

        try:
            while True:
                complete = self.step()
                if complete:
                    break

                # 4. Wait / Simulate charging
                if self.simulated:
                    # In simulation, step faster than real time for better UX
                    # Ensure at least 1 simulation step is run even for sub-second intervals
                    sim_steps = max(1, int(self.interval))
                    step_size = self.interval / sim_steps
                    for _ in range(sim_steps):
                        self.simulate_step(step_size)
                        time.sleep(0.2 * step_size)
                else:
                    time.sleep(self.interval)

        except KeyboardInterrupt:
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_RED}Operation interrupted by user.{COLOR_RESET}\n")
        except Exception as e:
            sys.stdout.write(f"\n{COLOR_BOLD}{COLOR_RED}Fatal Error: {e}{COLOR_RESET}\n")
        finally:
            self.shutdown()


def run_interactive_wizard(defaults: dict) -> dict:
    import json
    settings = dict(defaults)
    
    while True:
        sys.stdout.write(CLEAR_SCREEN)
        sys.stdout.write(f"{COLOR_BOLD}{COLOR_BLUE}=== LiFePO4 Balancer Configuration Wizard ==={COLOR_RESET}\n\n")
        
        recharge_th = min(settings['target'] - settings['delta'], 3.28)
        sys.stdout.write(f" [1] Target Voltage:        {COLOR_BOLD}{settings['target']:.3f} V{COLOR_RESET}\n")
        sys.stdout.write(f" [2] Balance Delta:         {settings['delta'] * 1000:.0f} mV (Recharge Threshold: {recharge_th:.3f} V)\n")
        sys.stdout.write(f" [3] Balancing Mode:        {COLOR_BOLD}{settings['mode'].upper()}{COLOR_RESET}\n")
        if settings['mode'] == 'parallel':
            sys.stdout.write(f" [4] Parallel Delta:       {settings['parallel_delta'] * 1000:.0f} mV\n")
        else:
            sys.stdout.write(f" [4] Parallel Delta:       (N/A - Single Mode)\n")
        sys.stdout.write(f" [5] Batches Configuration: {COLOR_BOLD}{settings['batches']}{COLOR_RESET}\n")
        sys.stdout.write(f" [6] OCV Check Interval:    {settings['ocv_interval']:.1f} s\n")
        sys.stdout.write(f" [7] Voltage Headroom:      {settings['headroom']:.2f} V\n")
        sys.stdout.write(f" [8] Termination Current:   {settings['termination_current']:.3f} A\n")
        sys.stdout.write(f" [9] Max Cell Current Limit:{settings['max_cell_current']:.2f} A\n")
        sys.stdout.write(f" [10] Run Mode:             {COLOR_BOLD}{'MONITOR-ONLY' if settings['monitor'] else 'CHARGING/BALANCING'}{COLOR_RESET}\n")
        sys.stdout.write(f" [11] Force Simulation:     {COLOR_BOLD}{'YES' if settings['sim'] else 'NO (autodetect hardware)'}{COLOR_RESET}\n")
        sys.stdout.write(f" [12] Log Directory:        {COLOR_BOLD}{settings['log_dir'] if settings['log_dir'] else '(Disabled)'}{COLOR_RESET}\n")
        sys.stdout.write("\n")
        sys.stdout.write(f" [L] Load Config File       [S] Save Config File\n")
        sys.stdout.write(f" {COLOR_BOLD}{COLOR_GREEN}[G] START BALANCING{COLOR_RESET}        {COLOR_BOLD}{COLOR_RED}[Q] EXIT{COLOR_RESET}\n\n")
        
        sys.stdout.write("Select an option to edit, or press 'G' to start: ")
        sys.stdout.flush()
        
        try:
            choice = input().strip().upper()
        except KeyboardInterrupt:
            sys.stdout.write("\nExiting Wizard.\n")
            sys.exit(0)
            
        if choice == 'Q':
            sys.stdout.write("\nExiting.\n")
            sys.exit(0)
        elif choice == 'G':
            return settings
        elif choice == '1':
            try:
                val = input(f"Enter Target Voltage [{settings['target']}]: ").strip()
                if val:
                    settings['target'] = float(val)
            except ValueError:
                pass
        elif choice == '2':
            try:
                val = input(f"Enter Balance Delta in Volts [{settings['delta']}]: ").strip()
                if val:
                    settings['delta'] = float(val)
            except ValueError:
                pass
        elif choice == '3':
            settings['mode'] = 'single' if settings['mode'] == 'parallel' else 'parallel'
        elif choice == '4':
            try:
                val = input(f"Enter Parallel Delta in Volts [{settings['parallel_delta']}]: ").strip()
                if val:
                    settings['parallel_delta'] = float(val)
            except ValueError:
                pass
        elif choice == '5':
            sys.stdout.write("\nSelect Batches Layout:\n")
            sys.stdout.write(" 1: both (Batch 1: 1-8, Batch 2: 9-16)\n")
            sys.stdout.write(" 2: 1 (Batch 1: 1-8 only)\n")
            sys.stdout.write(" 3: 2 (Batch 2: 9-16 only)\n")
            sys.stdout.write(" 4: 4/4 (Batch 1: 1-4, Batch 2: 5-8)\n")
            b_choice = input(f"Select layout [1-4]: ").strip()
            if b_choice == '1':
                settings['batches'] = 'both'
            elif b_choice == '2':
                settings['batches'] = '1'
            elif b_choice == '3':
                settings['batches'] = '2'
            elif b_choice == '4':
                settings['batches'] = '4/4'
        elif choice == '6':
            try:
                val = input(f"Enter OCV Check Interval in seconds [{settings['ocv_interval']}]: ").strip()
                if val:
                    settings['ocv_interval'] = float(val)
            except ValueError:
                pass
        elif choice == '7':
            try:
                val = input(f"Enter Voltage Headroom in Volts [{settings['headroom']}]: ").strip()
                if val:
                    settings['headroom'] = float(val)
            except ValueError:
                pass
        elif choice == '8':
            try:
                val = input(f"Enter Termination Current in Amperes [{settings['termination_current']}]: ").strip()
                if val:
                    settings['termination_current'] = float(val)
            except ValueError:
                pass
        elif choice == '9':
            try:
                val = input(f"Enter Max Cell Current in Amperes [{settings['max_cell_current']}]: ").strip()
                if val:
                    settings['max_cell_current'] = float(val)
            except ValueError:
                pass
        elif choice == '10':
            settings['monitor'] = not settings['monitor']
        elif choice == '11':
            settings['sim'] = not settings['sim']
        elif choice == '12':
            val = input(f"Enter Log Directory [{settings['log_dir']}]: ").strip()
            settings['log_dir'] = val
        elif choice == 'L':
            path = input("Enter config file path [balancer_config.json]: ").strip()
            if not path:
                path = "balancer_config.json"
            try:
                with open(path, "r") as f:
                    loaded = json.load(f)
                for k, v in loaded.items():
                    if k in settings:
                        settings[k] = v
                sys.stdout.write(f"\n{COLOR_GREEN}Configuration loaded successfully from {path}!{COLOR_RESET}\n")
                time.sleep(1.5)
            except Exception as e:
                sys.stdout.write(f"\n{COLOR_RED}Error loading config: {e}{COLOR_RESET}\n")
                time.sleep(2.0)
        elif choice == 'S':
            path = input("Enter save path [balancer_config.json]: ").strip()
            if not path:
                path = "balancer_config.json"
            try:
                with open(path, "w") as f:
                    json.dump(settings, f, indent=4)
                sys.stdout.write(f"\n{COLOR_GREEN}Configuration saved successfully to {path}!{COLOR_RESET}\n")
                time.sleep(1.5)
            except Exception as e:
                sys.stdout.write(f"\n{COLOR_RED}Error saving config: {e}{COLOR_RESET}\n")
                time.sleep(2.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="M300 & SPD3303X Battery Balancer")
    parser.add_argument(
        "--config",
        type=str,
        help="Path to JSON configuration file to load settings from",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force interactive TUI configuration menu at startup",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Force simulation mode without connecting to hardware",
    )
    parser.add_argument(
        "--target",
        type=float,
        default=3.50,
        help="Target balance voltage in Volts (default: 3.50)",
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
        choices=["1", "2", "both", "4/4"],
        default="both",
        help="Select which cell batch to balance: 1 (bottom 8), 2 (top 8), both (default), or 4/4 (bottom 4/top 4 split)",
    )
    parser.add_argument(
        "--ocv-interval",
        type=float,
        default=60.0,
        help="Interval in seconds between open-circuit voltage (OCV) measurements (default: 60.0)",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Run in monitor-only mode (read and display voltages periodically, PSU outputs remain OFF, actuator relays remain OPEN)",
    )
    parser.add_argument(
        "--headroom",
        type=float,
        default=0.15,
        help="Voltage headroom in Volts added to PSU setpoint to overcome wiring and relay resistance (default: 0.15)",
    )
    parser.add_argument(
        "--termination-current",
        type=float,
        default=0.05,
        help="Per-cell current threshold in Amperes for charge termination (default: 0.05)",
    )
    parser.add_argument(
        "--log-dir",
        type=str,
        default="logs",
        help="Directory to save CSV telemetry logs (default: logs, set to empty string to disable)",
    )

    args = parser.parse_args()

    # Load defaults from config file if specified
    config_settings = {}
    if args.config:
        try:
            with open(args.config, "r") as f:
                import json
                config_settings = json.load(f)
            print(f"Loaded configuration from {args.config}")
        except Exception as e:
            print(f"Error loading config file {args.config}: {e}")
            sys.exit(1)

    target = config_settings.get("target", args.target)
    delta = config_settings.get("delta", args.delta)
    parallel_delta = config_settings.get("parallel_delta", args.parallel_delta)
    sim = config_settings.get("sim", args.sim)
    interval = config_settings.get("interval", args.interval)
    mode = config_settings.get("mode", args.mode)
    max_voltage = config_settings.get("max_voltage", args.max_voltage)
    max_current = config_settings.get("max_current", args.max_current)
    max_cell_current = config_settings.get("max_cell_current", args.max_cell_current)
    batches = config_settings.get("batches", args.batches)
    ocv_interval = config_settings.get("ocv_interval", args.ocv_interval)
    monitor = config_settings.get("monitor", args.monitor)
    headroom = config_settings.get("headroom", args.headroom)
    termination_current = config_settings.get("termination_current", args.termination_current)
    log_dir = config_settings.get("log_dir", args.log_dir)

    # Launch interactive wizard if no arguments are passed, or if --interactive is forced
    is_interactive = args.interactive or (len(sys.argv) == 1)

    if is_interactive:
        settings = run_interactive_wizard({
            "target": target,
            "delta": delta,
            "parallel_delta": parallel_delta,
            "sim": sim,
            "interval": interval,
            "mode": mode,
            "max_voltage": max_voltage,
            "max_current": max_current,
            "max_cell_current": max_cell_current,
            "batches": batches,
            "ocv_interval": ocv_interval,
            "monitor": monitor,
            "headroom": headroom,
            "termination_current": termination_current,
            "log_dir": log_dir,
        })
        target = settings["target"]
        delta = settings["delta"]
        parallel_delta = settings["parallel_delta"]
        sim = settings["sim"]
        interval = settings["interval"]
        mode = settings["mode"]
        max_voltage = settings["max_voltage"]
        max_current = settings["max_current"]
        max_cell_current = settings["max_cell_current"]
        batches = settings["batches"]
        ocv_interval = settings["ocv_interval"]
        monitor = settings["monitor"]
        headroom = settings["headroom"]
        termination_current = settings["termination_current"]
        log_dir = settings["log_dir"]

    app = BalancerApp(
        target_voltage=target,
        balance_delta=delta,
        parallel_delta=parallel_delta,
        simulated=sim,
        interval=interval,
        mode=mode,
        max_voltage=max_voltage,
        max_current=max_current,
        max_cell_current=max_cell_current,
        batches=batches,
        ocv_interval=ocv_interval,
        monitor=monitor,
        headroom=headroom,
        termination_current=termination_current,
        log_dir=log_dir,
    )
    app.run()


if __name__ == "__main__":
    main()
