#!/usr/bin/env python3
"""Tests for the plot geometry helpers in nvtemp.py.

Run from the nvtemp directory:  python3 -m unittest discover -s tests -v
These tests do not need curses, a terminal or a GPU.
"""
import importlib.util
import os
import sys
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "nvtemp.py")
spec = importlib.util.spec_from_file_location("nvtemp_app", APP)
nvtemp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nvtemp)


class ValueToRowTest(unittest.TestCase):
    def test_extremes_land_on_first_and_last_row(self):
        self.assertEqual(nvtemp.value_to_row(90, 90, 10), 0)
        self.assertEqual(nvtemp.value_to_row(0, 90, 10), 9)

    def test_always_inside_the_grid(self):
        for rows in range(1, 40):
            for maxv in (0, 1, 90, 120):
                for v in (-50, -1, 0, 0.5, 33, 89.9, 90, 95, 1000, 1e9):
                    row = nvtemp.value_to_row(v, maxv, rows)
                    self.assertGreaterEqual(row, 0)
                    self.assertLess(row, max(1, rows),
                                    f"rows={rows} maxv={maxv} v={v} -> {row}")

    def test_higher_temperature_is_higher_on_screen(self):
        self.assertLess(nvtemp.value_to_row(85, 90, 20),
                        nvtemp.value_to_row(40, 90, 20))

    def test_single_row_collapses_to_zero(self):
        self.assertEqual(nvtemp.value_to_row(40, 90, 1), 0)
        self.assertEqual(nvtemp.value_to_row(40, 90, 0), 0)


class RowToValueTest(unittest.TestCase):
    def test_inverse_of_value_to_row(self):
        for rows in (2, 3, 10, 25):
            for row in range(rows):
                v = nvtemp.row_to_value(row, 90, rows)
                self.assertLessEqual(abs(nvtemp.value_to_row(v, 90, rows) - row), 1)


class BuildTraceTest(unittest.TestCase):
    def test_never_indexes_outside_the_grid(self):
        for rows in range(0, 30):
            for cols in ([], [30], [30, 31, 32], [10, 90, 0, 45, 89],
                         [None, 60, None, 60]):
                grid = nvtemp.build_trace(cols, 90, rows)
                self.assertEqual(len(grid), max(0, rows))

    def test_small_terminal_still_draws_a_line(self):
        grid = nvtemp.build_trace([30, 30, 30], 90, 2)
        self.assertEqual(len(grid), 2)
        self.assertEqual(sum(cell is not None for row in grid for cell in row), 3)

    def test_flat_series_is_a_horizontal_run(self):
        grid = nvtemp.build_trace([50] * 6, 100, 10)
        drawn = [(r, c) for r in range(10) for c in range(6)
                 if grid[r][c] is not None]
        self.assertEqual(len(drawn), 6)
        self.assertEqual({ch for r, c in drawn for ch in [grid[r][c]]}, {"─"})
        self.assertEqual(len({r for r, _ in drawn}), 1)

    def test_rise_uses_corner_and_vertical(self):
        grid = nvtemp.build_trace([10, 90], 90, 9)
        chars = {grid[r][c] for r in range(9) for c in range(2)
                 if grid[r][c] is not None}
        self.assertIn("┘", chars)
        self.assertIn("┌", chars)
        self.assertIn("│", chars)

    def test_missing_samples_leave_cells_empty(self):
        grid = nvtemp.build_trace([None, None], 90, 5)
        self.assertTrue(all(cell is None for row in grid for cell in row))


if __name__ == "__main__":
    unittest.main()
