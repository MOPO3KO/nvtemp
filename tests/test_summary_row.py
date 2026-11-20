#!/usr/bin/env python3
"""Tests for the compact summary row drawn above the plots.

The row must put TEMP, MEM, GPU load and VRAM for every card, the selected one
included, in the same columns — anchored to the longest card name on screen, not
to the right edge of the window. No curses, no terminal, no GPU needed: a tiny
fake window collects the characters.

Run from the nvtemp directory:

    python3 -m unittest discover -s tests -p 'test_*.py' -v
"""
import importlib.util
import os
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "nvtemp.py")


class FairWindow:
    """Minimal stand-in for a curses window: remembers written characters."""

    def __init__(self, rows, cols):
        self.rows = rows
        self.cols = cols
        self.cells = {}

    def getmaxyx(self):
        return (self.rows, self.cols)

    def addnstr(self, y, x, s, n, attr=0):
        if y < 0 or y >= self.rows or x < 0 or x >= self.cols:
            raise AssertionError(f"write outside the window at ({y},{x})")
        for i, ch in enumerate(s[:n]):
            if x + i >= self.cols:
                raise AssertionError(f"write past the right edge at ({y},{x})")
            self.cells[(y, x + i)] = ch

    def line(self, y):
        return "".join(self.cells.get((y, x), " ") for x in range(self.cols))


def load_app():
    spec = importlib.util.spec_from_file_location("nvtemp_app", APP)
    nvtemp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nvtemp)
    return nvtemp


class SummaryRowTest(unittest.TestCase):
    def setUp(self):
        self.nvtemp = load_app()
        # curses.color_pair() needs initscr(); the row only uses it as a value
        self.nvtemp.curses.color_pair = lambda n: 0

    def render(self, cols, names=None, name="Fake GPU", g=78, m=36, thr=90,
               idx=0, selected=False, util=45, used=12288, total=24576,
               indices=None):
        if indices is None:
            indices = [idx]
        if names is None:
            names = {i: name for i in indices}
        nw, widths = self.nvtemp.summary_layout(indices, names, cols)
        win = FairWindow(24, cols)
        self.nvtemp.draw_summary_row(win, 0, idx, names.get(idx, "N/A"),
                                     g, m, thr, nw, widths,
                                     util=util, used=used, total=total,
                                     selected=selected)
        return win.line(0)

    # ---- column geometry -------------------------------------------------

    def test_columns_start_after_the_longest_name_on_screen(self):
        # The layout is shared, so a short name leaves its numbers in the same
        # column as a long name: values sit next to the name block, not at the
        # far right edge.
        names = {0: "A", 1: "Tesla V100-SXM2-32GB"}
        short = self.render(120, names=names, idx=0, indices=[0, 1])
        long_name = self.render(120, names=names, idx=1, indices=[0, 1])
        self.assertEqual(short.index("TEMP"), long_name.index("TEMP"))
        self.assertLess(short.index("TEMP"), 40)   # not pushed to the edge

    def test_longest_name_wins_for_one_card_only(self):
        short = self.render(120, name="A")
        self.assertLess(short.index("TEMP"), 20)

    def test_wider_window_does_not_move_the_columns(self):
        narrow = self.render(90, name="Tesla V100-SXM2-32GB")
        wide = self.render(200, name="Tesla V100-SXM2-32GB")
        self.assertEqual(narrow.index("TEMP"), wide.index("TEMP"))

    # ---- columns offered -------------------------------------------------

    def test_all_four_columns_on_a_wide_window(self):
        line = self.render(120, name="Tesla V100")
        self.assertIn("TEMP  78°C", line)
        self.assertIn("MEM  36°C", line)
        self.assertIn("GPU  45%", line)
        self.assertIn("VRAM 12.0G/24.0G", line)
        # search only the part after the name block: "GPU" also appears in the
        # card name itself, which would give a false ordering
        numbers = line[line.index("TEMP"):]
        order = [numbers.index(c) for c in ("TEMP", "MEM", "GPU", "VRAM")]
        self.assertEqual(order, sorted(order), "columns must stay in order")

    def test_narrow_window_keeps_first_columns(self):
        line = self.render(46, name="Fake GPU 0")
        self.assertIn("TEMP", line)
        # trailing columns are dropped whole, never cut mid-word
        self.assertNotIn("VR", line.replace("VRAM", ""))

    def test_extra_values_missing_fall_back_to_na(self):
        line = self.render(120, util=None, used=None, total=None)
        self.assertIn("GPU N/A%", line)
        self.assertIn("VRAM   N/A/  N/A", line)

    # ---- values and marking ---------------------------------------------

    def test_selected_card_is_marked_and_stays_in_the_list(self):
        self.assertTrue(self.render(90, selected=True).startswith(">Device 0"))
        self.assertTrue(self.render(90, selected=False).startswith(" Device 0"))

    def test_values_are_right_aligned(self):
        line = self.render(120, g=7, m=100, util=5)
        self.assertIn("TEMP   7°C", line)
        self.assertIn("MEM 100°C", line)
        self.assertIn("GPU   5%", line)

    def test_never_writes_outside_a_narrow_window(self):
        for cols in (24, 30, 46, 60, 90, 200):
            self.render(cols, name="Tesla V100-SXM2-32GB")  # must not raise

    def test_too_narrow_window_draws_nothing(self):
        self.assertEqual(self.render(20).strip(), "")

    def test_never_overwrites_the_footer_row(self):
        nw, widths = self.nvtemp.summary_layout([0], {0: "x"}, 90)
        win = FairWindow(10, 90)
        self.nvtemp.draw_summary_row(win, 9, 0, "x", 50, 50, 90, nw, widths)
        self.assertEqual(win.line(9).strip(), "")


if __name__ == "__main__":
    unittest.main()
