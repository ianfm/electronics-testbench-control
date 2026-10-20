from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock, call
from routines.m300_battery_balancer import BalancerApp, BatchState


class BalancerAppTests(unittest.TestCase):
    def test_calculate_cell_currents_balanced(self) -> None:
        app = BalancerApp(simulated=True)
        # 8 cells, all at 3.20V, total current is 3.2A
        voltages = [3.20] * 8
        active_cells = list(range(1, 9))
        currents = app.calculate_cell_currents(voltages, active_cells, 3.2, internal_resistance=0.05)
        # All cells should receive equal current: 3.2A / 8 = 0.4A
        for i, c in enumerate(currents):
            self.assertAlmostEqual(c, 0.4)

    def test_calculate_cell_currents_unbalanced(self) -> None:
        app = BalancerApp(simulated=True)
        # 2 active cells: Cell 1 is 3.20V, Cell 2 is 3.22V, total current is 2.0A
        voltages = [3.20, 3.22] + [3.3] * 6
        active_cells = [1, 2]
        currents = app.calculate_cell_currents(voltages, active_cells, 2.0, internal_resistance=0.05)
        # Expected:
        # V_common = (2.0 * 0.05 + 3.20 + 3.22) / 2 = 3.26V
        # I_1 = (3.26 - 3.20) / 0.05 = 1.20A
        # I_2 = (3.26 - 3.22) / 0.05 = 0.80A
        self.assertAlmostEqual(currents[0], 1.20)
        self.assertAlmostEqual(currents[1], 0.80)
        self.assertEqual(currents[2], 0.0)

    def test_dynamic_current_limiting_parallel(self) -> None:
        # max_cell_current = 1.2A, max_current = 3.2A
        app = BalancerApp(
            simulated=True,
            mode="parallel",
            parallel_delta=0.025,
            max_cell_current=1.2,
            max_current=3.2,
        )
        
        # Test Case 1: Unbalanced active cells, 3.20V and 3.22V.
        # Max operating setpoint starts at self.max_current - 0.1 = 3.1A.
        # But cell voltage delta (20mV) will restrict it.
        # sum_v = 6.42V, v_min = 3.20V, M = 2, R = 0.05.
        # i_total_limit = 2 * 1.2 - (6.42 - 6.40) / 0.05 = 2.4 - 0.4 = 2.0A.
        # So it should be capped at 2.0A.
        voltages = [3.20, 3.22] + [3.30] * 6
        app.update_batch_state(app.batch1, voltages)
        self.assertEqual(app.batch1.active_cells, [1, 2])
        # Bulk charge current limit should be set to 2.0A
        self.assertAlmostEqual(app.batch1.current_set, 2.0)

    def test_dynamic_current_limiting_highly_unbalanced(self) -> None:
        app = BalancerApp(
            simulated=True,
            mode="parallel",
            parallel_delta=0.05,
            max_cell_current=1.2,
            max_current=3.2,
        )
        # Test Case 2: Voltages 3.20V and 3.24V (delta 40mV, strictly < 50mV parallel delta).
        # i_total_limit = 2 * 1.2 - (6.44 - 6.40) / 0.05 = 2.4 - 0.8 = 1.6A.
        voltages = [3.20, 3.24] + [3.30] * 6
        app.update_batch_state(app.batch1, voltages)
        self.assertEqual(app.batch1.active_cells, [1, 2])
        self.assertAlmostEqual(app.batch1.current_set, 1.6)

    def test_strict_safety_trips_cell_overvoltage(self) -> None:
        app = BalancerApp(simulated=True, max_voltage=3.65)
        # Set a cell voltage above the max_voltage limit
        app.sim_voltages[0] = 3.66
        with self.assertRaises(ValueError) as ctx:
            app.step()
        self.assertIn("exceeded maximum safety limit of 3.65", str(ctx.exception))

    def test_strict_safety_trips_psu_overcurrent(self) -> None:
        app = BalancerApp(simulated=True, max_current=3.2)
        app.sim_voltages = [3.20] * 16
        app.batch1.current_reading = 3.25  # Exceeds 3.2A
        
        with patch.object(app, 'update_batch_state'):
            with patch.object(app, 'measure_cell_voltages', return_value=[3.20]*8):
                # Cycle 1: Increments cycle count to 1
                app.step()
                # Cycle 2: Exceeds threshold again, triggering ValueError
                with self.assertRaises(ValueError) as ctx:
                    app.step()
        self.assertIn("PSU Channel 1 current 3.250 A exceeded safety limit of 3.200 A", str(ctx.exception))

    def test_strict_safety_trips_calculated_cell_overcurrent(self) -> None:
        app = BalancerApp(simulated=True, max_cell_current=1.2, mode="parallel")
        # Ensure that during step, the calculated cell current exceeds 1.2A
        # We can bypass update_batch_state setting the limit by manually setting current_reading to 3.0A
        # for a batch that has 2 active cells with a large delta (e.g. 3.20V and 3.24V)
        app.sim_voltages[0] = 3.20
        app.sim_voltages[1] = 3.24
        app.batch1.active_cells = [1, 2]
        app.batch1.current_reading = 3.0
        
        # Mock update_batch_state to do nothing so it doesn't overwrite our manual active_cells and current_reading
        with patch.object(app, 'update_batch_state'):
            with patch.object(app, 'measure_cell_voltages', return_value=[3.20, 3.24] + [3.3]*6):
                # Cycle 1: Increments cycle count to 1
                app.step()
                # Cycle 2: Exceeds threshold again, triggering ValueError
                with self.assertRaises(ValueError) as ctx:
                    app.step()
        self.assertIn("exceeded individual cell current safety limit of 1.200 A", str(ctx.exception))
    def test_batch_filtering(self) -> None:
        # Initialize app with batches="1" (meaning Batch 2 is skipped/completed)
        app = BalancerApp(simulated=True, batches="1")
        self.assertEqual(app.batch2.status, "COMPLETED")
        self.assertEqual(app.batch1.status, "MONITORING")

        # Mock measure_cell_voltages to verify that it is only called for batch1
        with patch.object(app, 'measure_cell_voltages', return_value=[3.20] * 8) as mock_measure:
            # We mock print_dashboard so it doesn't try to output to terminal
            with patch.object(app, 'print_dashboard'):
                app.step()
            
            # Should only measure batch1 cell voltages
            mock_measure.assert_called_once_with(app.batch1, open_circuit=True)
    def test_batches_split_4_4(self) -> None:
        # Initialize app in "4/4" batches split
        app = BalancerApp(simulated=True, batches="4/4")
        self.assertEqual(len(app.batch1.mux_channels), 4)
        self.assertEqual(len(app.batch2.mux_channels), 4)
        self.assertEqual(app.batch1.status, "MONITORING")
        self.assertEqual(app.batch2.status, "MONITORING")

        # Mock print_dashboard and check step
        with patch.object(app, 'print_dashboard'):
            # It should complete a step successfully without raising any exceptions
            completed = app.step()
            self.assertFalse(completed)

        # In 4/4 mode, mock simulated step should work as expected
        app.simulate_step(1.0)
        # Check that active voltages are updated
        self.assertGreater(app.sim_voltages[0], 2.90)
    def test_strict_safety_trips_negative_voltage(self) -> None:
        app = BalancerApp(simulated=True)
        # Set a cell voltage to negative
        app.sim_voltages[0] = -0.1
        with self.assertRaises(ValueError) as ctx:
            app.step()
        self.assertIn("voltage -0.100 V is negative!", str(ctx.exception))

    def test_periodic_ocv_and_cold_switching(self) -> None:
        app = BalancerApp(simulated=True, interval=5.0, ocv_interval=60.0)
        self.assertTrue(app.trigger_ocv_next)
        
        # 1. First step should run OCV check (since trigger_ocv_next is True)
        with patch.object(app, 'measure_cell_voltages', return_value=[3.20] * 8) as mock_measure:
            with patch.object(app, 'print_dashboard'):
                app.step()
            self.assertEqual(
                mock_measure.call_args_list,
                [call(app.batch1, open_circuit=True), call(app.batch2, open_circuit=True)]
            )
            self.assertFalse(app.trigger_ocv_next)

        # 2. Second step should NOT run OCV check (open_circuit=False)
        with patch.object(app, 'measure_cell_voltages', return_value=[3.20] * 8) as mock_measure:
            with patch.object(app, 'print_dashboard'):
                app.step()
            self.assertEqual(
                mock_measure.call_args_list,
                [call(app.batch1, open_circuit=False), call(app.batch2, open_circuit=False)]
            )

        # 3. Simulate one of the active cells under charge reaching target voltage (3.60V)
        # Set a low current limit to satisfy CV absorption termination criteria
        app.batch1.active_cells = [1]
        app.batch1.status = "CHARGING"
        app.batch1.current_set = 0.04
        with patch.object(app, 'measure_cell_voltages', return_value=[3.60] + [3.20]*7) as mock_measure:
            with patch.object(app, 'print_dashboard'):
                app.step()
            self.assertTrue(app.trigger_ocv_next)

        # 4. The following step should now run an OCV check (open_circuit=True)
        with patch.object(app, 'measure_cell_voltages', return_value=[3.20] * 8) as mock_measure:
            with patch.object(app, 'print_dashboard'):
                app.step()
            self.assertEqual(
                mock_measure.call_args_list,
                [call(app.batch1, open_circuit=True), call(app.batch2, open_circuit=True)]
            )
            self.assertFalse(app.trigger_ocv_next)


    def test_completed_batch_retains_last_voltages(self) -> None:
        app = BalancerApp(simulated=True, batches="both")
        # Step 1: Normal step, record voltages
        with patch.object(app, 'measure_cell_voltages') as mock_measure:
            mock_measure.side_effect = [[3.59]*8, [3.20]*8]
            with patch.object(app, 'print_dashboard') as mock_print:
                app.step()
                # Dashboard should receive 3.59V for batch 1, 3.20V for batch 2
                mock_print.assert_called_once_with([3.59]*8 + [3.20]*8)

        # Set batch 1 to completed
        app.batch1.status = "COMPLETED"
        app.batch1.last_voltages = [3.59]*8

        # Step 2: Batch 1 is COMPLETED, so it shouldn't measure batch 1, but should reuse its voltages
        with patch.object(app, 'measure_cell_voltages') as mock_measure:
            mock_measure.side_effect = [[3.21]*8] # only batch 2 is measured
            with patch.object(app, 'print_dashboard') as mock_print:
                app.step()
                # Dashboard should show preserved 3.59V for batch 1, and new 3.21V for batch 2
                mock_print.assert_called_once_with([3.59]*8 + [3.21]*8)

    def test_monitor_mode(self) -> None:
        app = BalancerApp(simulated=True, monitor=True)
        # Verify that in monitor mode:
        # 1. step() returns False (always continues monitoring)
        # 2. measure_cell_voltages is called with open_circuit=True
        with patch.object(app, 'measure_cell_voltages', return_value=[3.20] * 8) as mock_measure:
            with patch.object(app, 'print_dashboard') as mock_print:
                completed = app.step()
                self.assertFalse(completed)
                mock_measure.assert_has_calls([
                    call(app.batch1, open_circuit=True),
                    call(app.batch2, open_circuit=True)
                ])
                mock_print.assert_called_once_with([3.20]*16)
        
        # Verify batch parameters remain in MONITORING / OFF state
        self.assertEqual(app.batch1.status, "MONITORING")
        self.assertEqual(app.batch1.charge_mode, "OFF")
        self.assertEqual(app.batch1.active_cells, [])
        self.assertEqual(app.batch1.current_reading, 0.0)

    def test_connection_error_fallback_to_simulation(self) -> None:
        app = BalancerApp(simulated=False)
        with patch('routines.m300_battery_balancer.discover_instruments') as mock_discover:
            mock_discover.side_effect = RuntimeError("Could not connect to VISA address")
            with patch('time.sleep'):  # prevent waiting 2 seconds in test
                app.connect()
        self.assertTrue(app.simulated)
        self.assertEqual(app.connection_error, "Could not connect to VISA address")

    def test_cc_cv_absorption_termination(self) -> None:
        import time
        app = BalancerApp(simulated=True, target_voltage=3.50, termination_current=0.05)
        # Setup batch 1: cell 1 is charging and near target, current is low (0.04A)
        app.batch1.status = "CHARGING"
        app.batch1.active_cells = [1]
        app.batch1.current_reading = 0.04
        
        # We run a step with need_ocv=False (so we don't trigger OCV, meaning it's an under-charge step)
        app.trigger_ocv_next = False
        app.last_ocv_time = time.time()
        
        with patch.object(app, 'print_dashboard'):
            with patch.object(app, 'measure_cell_voltages', return_value=[3.49] + [3.3]*7):
                app.step()
                
        # Cell 1 should be marked completed
        self.assertIn(1, app.batch1.completed_cells)
        self.assertTrue(app.trigger_ocv_next)

    def test_run_interactive_wizard_exit_immediately(self) -> None:
        from routines.m300_battery_balancer import run_interactive_wizard
        defaults = {
            "target": 3.50,
            "delta": 0.02,
            "parallel_delta": 0.01,
            "sim": False,
            "interval": 5.0,
            "mode": "parallel",
            "max_voltage": 3.65,
            "max_current": 3.2,
            "max_cell_current": 1.2,
            "batches": "4/4",
            "ocv_interval": 30.0,
            "monitor": False,
            "headroom": 0.15,
            "termination_current": 0.05,
            "log_dir": "logs",
        }
        with patch('builtins.input', side_effect=['G']):
            res = run_interactive_wizard(defaults)
        self.assertEqual(res, defaults)

    def test_cv_absorption_regulation_high_current(self) -> None:
        import time
        app = BalancerApp(simulated=False, target_voltage=3.50, headroom=0.15, termination_current=0.05)
        # Setup batch 1: cell 1 is charging and reaches target voltage (3.52V), but current is high (1.0A)
        app.batch1.status = "CHARGING"
        app.batch1.charge_mode = "ABSORPTION"
        app.batch1.active_cells = [1]
        app.batch1.current_set = 1.2
        app.batch1.voltage_set = 3.65
        
        app.psu = MagicMock()
        app.psu.get_current.return_value = 1.0
        app.psu.get_voltage.return_value = 3.65
        
        app.trigger_ocv_next = False
        app.last_ocv_time = time.time()
        
        with patch.object(app, 'print_dashboard'):
            with patch.object(app, 'measure_cell_voltages', return_value=[3.52] + [3.3]*7):
                app.step()
                
        # Cell 1 should NOT be completed because current is 1.0A (> 0.05A)
        self.assertNotIn(1, app.batch1.completed_cells)
        # OCV check should NOT be triggered
        self.assertFalse(app.trigger_ocv_next)
        # Voltage setpoint should be dynamically adjusted/trimmed down from 3.65V
        # Expected:
        # Base v_set: 3.50 + 0.15 * (1.0/1.2) = 3.625V
        # Overshoot: 3.52 - 3.50 = 0.02V
        # Feedback trimmed: v_set - overshoot = 3.625 - 0.02 = 3.605V
        self.assertAlmostEqual(app.batch1.voltage_set, 3.605)
        app.psu.set_voltage.assert_called_once_with(1, 3.605)

    def test_no_relaxation_trips_during_active_charging(self) -> None:
        import time
        app = BalancerApp(simulated=True, target_voltage=3.50, balance_delta=0.02, parallel_delta=0.01)
        # Setup batch 1: cell 1 is active charging, cell 2 is completed, cell 3 is idle/disconnected
        app.batch1.status = "CHARGING"
        app.batch1.active_cells = [1]
        app.batch1.completed_cells = [2]
        app.batch1.current_reading = 1.0
        app.batch1.current_set = 1.0
        
        app.trigger_ocv_next = False
        app.last_ocv_time = time.time()
        
        # Mock measurements during active charging:
        # - Cell 1 (active) is at terminal voltage 3.48V
        # - Cell 2 (completed) has relaxed to 3.45V (< target_voltage - balance_delta = 3.48V)
        # - Cell 3 (idle) has relaxed to 3.40V (< active cell terminal voltage 3.48V - parallel_delta)
        # In the active charging step, these relaxations should NOT trigger OCV next.
        with patch.object(app, 'print_dashboard'):
            with patch.object(app, 'measure_cell_voltages', return_value=[3.48, 3.45, 3.40] + [3.3]*5):
                app.step()
                
        self.assertFalse(app.trigger_ocv_next)
        # Cell 2 should remain in completed_cells (de-completion only happens during OCV steps)
        self.assertIn(2, app.batch1.completed_cells)

    def test_csv_telemetry_logging(self) -> None:
        import os
        import tempfile
        import csv
        
        # Create a temporary directory for logging
        with tempfile.TemporaryDirectory() as tmpdir:
            app = BalancerApp(simulated=True, log_dir=tmpdir)
            app.connect()
            app.init_logging()
            
            # Verify the log file was created
            self.assertIsNotNone(app.log_file)
            self.assertTrue(os.path.exists(app.log_path))
            
            # Run a step (which will trigger write_log_entry)
            with patch.object(app, 'print_dashboard'):
                app.step()
                
            # Close the log to flush the files
            app.close_logging()
            
            # Read and verify the contents of the log file
            with open(app.log_path, "r", newline="") as f:
                reader = list(csv.reader(f))
                
            # Total columns = 1 (timestamp) + 4 (b1 settings) + 4 (b2 settings) + 16 (v cells) + 16 (i cells) + 16 (ir cells) + 16 (ah cells) = 73
            self.assertEqual(len(reader[0]), 73)
            self.assertEqual(reader[0][0], "timestamp")
            self.assertEqual(reader[0][9], "v_cell_01")
            self.assertEqual(reader[0][24], "v_cell_16")
            self.assertEqual(reader[0][25], "i_cell_01")
            self.assertEqual(reader[0][40], "i_cell_16")
            self.assertEqual(reader[0][41], "ir_cell_01")
            self.assertEqual(reader[0][56], "ir_cell_16")
            self.assertEqual(reader[0][57], "ah_cell_01")
            self.assertEqual(reader[0][72], "ah_cell_16")
            
            # Verify one data row is written containing the expected columns
            self.assertEqual(len(reader), 2)
            row = reader[1]
            self.assertEqual(len(row), 73)
            self.assertEqual(row[1], "CHARGING")

    def test_dashboard_vu_meters_and_ansi_formatting(self) -> None:
        import io
        import sys
        app = BalancerApp(simulated=True)
        # Capture stdout to verify dashboard output
        stdout_capture = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = stdout_capture
            app.print_dashboard([3.20] * 16)
        finally:
            sys.stdout = old_stdout
            
        output = stdout_capture.getvalue()
        self.assertIn("PSU V:", output)
        self.assertIn("PSU I:", output)
        self.assertIn("OFF", output)


if __name__ == "__main__":
    unittest.main()
