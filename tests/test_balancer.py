from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
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
                with self.assertRaises(ValueError) as ctx:
                    app.step()
        self.assertIn("exceeded individual cell current safety limit of 1.200 A", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
