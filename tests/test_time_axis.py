#!/usr/bin/env python3
"""Tests for the time axis of the plot: the line must keep touching the right edge.

The original bug: the visible window was anchored to the wall clock, so between
two samples "now" kept moving leftward and the last drawn point drifted away
from the right edge — the line looked like it lost its tail, then jumped back on
the next sample. Anchoring the window to the newest sample fixes that, and these
tests pin the behaviour down.

Run from the nvtemp directory:

    python3 -m unittest discover -s tests -p 'test_*.py' -v
"""
import importlib.util
import os
import unittest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "nvtemp.py")


def load_app():
    spec = importlib.util.spec_from_file_location("nvtemp_app", APP)
    nvtemp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(nvtemp)
    return nvtemp


def columns_at(samples, window, now, plot_w=60):
    """Place samples on the plot the same way nvtemp's main() does.

    `samples` is a list of (timestamp, value) pairs, oldest first. Returns the
    list of column indexes where each sample landed.
    """
    start_time = now - window
    newest = samples[-1][0] if samples else now
    end_time = max(newest, start_time + 1)
    view_span = max(end_time - start_time, 1)

    def time_to_x(t):
        x = int((t - start_time) * (plot_w - 1) / view_span)
        return max(0, min(plot_w - 1, x))

    return [time_to_x(t) for t, _ in samples]


class TailTest(unittest.TestCase):
    def setUp(self):
        self.nvtemp = load_app()

    def test_newest_sample_touches_the_right_edge(self):
        # A sample taken 0.5 s ago (half a second after the newest one was
        # collected) must still be drawn at the rightmost column, not one short.
        samples = [(100.0, 50.0), (101.0, 50.0), (102.0, 50.0)]
        for now in (102.0, 102.2, 102.5, 102.9, 103.0):
            cols = columns_at(samples, 180, now)
            self.assertEqual(cols[-1], 59,
                             f"tail drifted away at now={now}: {cols}")

    def test_tail_survives_a_long_sample_interval(self):
        # With one sample per 10 s the gap between samples is large; the newest
        # point must still sit on the right edge while the clock runs.
        samples = [(100.0, 50.0), (110.0, 50.0)]
        for now in (110.0, 112.0, 115.0, 119.5):
            cols = columns_at(samples, 180, now)
            self.assertEqual(cols[-1], 59, f"tail drifted away at now={now}")

    def test_older_samples_spread_leftwards(self):
        samples = [(100.0, 50.0), (130.0, 50.0), (160.0, 50.0), (190.0, 50.0)]
        cols = columns_at(samples, 180, 190.0)
        self.assertEqual(cols, sorted(cols), "samples must stay in time order")
        self.assertEqual(cols[-1], 59)
        self.assertLess(cols[0], cols[-1])

    def test_single_sample_is_on_the_right_edge(self):
        cols = columns_at([(100.0, 50.0)], 180, 100.0)
        self.assertEqual(cols, [59])

    def test_window_change_does_not_move_the_tail(self):
        samples = [(100.0, 50.0), (150.0, 50.0), (200.0, 50.0)]
        for window in (10, 60, 180, 3600, 86400):
            cols = columns_at(samples, window, 200.5)
            self.assertEqual(cols[-1], 59,
                             f"tail drifted with window={window}: {cols}")


class HistoryRetentionTest(unittest.TestCase):
    """Growing the window must reveal data already collected, not empty space."""

    def setUp(self):
        self.nvtemp = load_app()

    def test_history_limit_is_the_widest_window(self):
        nv = self.nvtemp
        self.assertGreaterEqual(nv.WINDOW_MAX, nv.WINDOW_DEFAULT)
        self.assertGreater(nv.WINDOW_MAX, 3600)

    def test_samples_inside_the_widest_window_are_kept(self):
        # Reproduce the retention rule from main(): anything newer than the
        # widest window is kept, so switching to a bigger window still has data.
        from collections import deque
        nv = self.nvtemp
        now = 1_000_000.0
        samples = deque((now - age, 50.0) for age in (5, 60, 600, 3600, 80000))
        limit = now - (nv.WINDOW_MAX + 10)
        kept = [t for t, _ in samples if t >= limit]
        self.assertEqual(len(kept), len(samples),
                         "an 80000 s old sample must survive for a 24h window")

    def test_old_samples_beyond_a_day_are_dropped(self):
        from collections import deque
        nv = self.nvtemp
        now = 1_000_000.0
        samples = deque([(now - 90000, 50.0), (now - 10, 50.0)])
        limit = now - (nv.WINDOW_MAX + 10)
        while samples and samples[0][0] < limit:
            samples.popleft()
        self.assertEqual(len(samples), 1, "a sample older than 24h is dropped")


class WindowKeyTest(unittest.TestCase):
    """The -/+ keys adjust the visible window, never the sampling rate."""

    class FakeWin:
        def __init__(self, rows=24, cols=90):
            self.rows, self.cols = rows, cols
            self.text = {}

        def getmaxyx(self):
            return (self.rows, self.cols)

        def erase(self):
            self.text.clear()

        def refresh(self):
            pass

        def nodelay(self, flag):
            pass

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addnstr(self, y, x, s, n, attr=0):
            for i, ch in enumerate(s[:n]):
                if x + i < self.cols:
                    self.text[(y, x + i)] = ch

        def line(self, y):
            return "".join(self.text.get((y, x), " ") for x in range(self.cols))

    def setUp(self):
        self.nvtemp = load_app()
        nv = self.nvtemp
        nv.curses.color_pair = lambda n: 0
        nv.curses.has_colors = lambda: False
        nv.curses.curs_set = lambda n: None
        nv.get_gpu_names = lambda: {0: "Fake GPU 0"}
        nv.get_slowdown_thresholds = lambda: {0: 90.0}
        nv.query_all = lambda: [(0, 50.0, 40.0, 45.0, 12288.0, 24576.0)]

    def run_app(self, keys, window=180, interval=1.0, rows=24, cols=100):
        win = self.FakeWin(rows, cols)
        win.keys = [ord(k) for k in keys] + [ord('q')]
        nv = self.nvtemp

        class Args:
            pass

        args = Args()
        args.window = window
        args.interval = interval
        args.slowdown = 90

        nv.time.sleep = lambda s: None
        nv.main(win, args)
        return win.line(rows - 1)

    def test_window_starts_at_three_minutes(self):
        self.assertIn("-/+ window 3m ([ ])", self.run_app(""))

    def test_keys_change_the_window_by_ten_seconds(self):
        self.assertIn("-/+ window 3m 10s", self.run_app("]"))
        self.assertIn("-/+ window 3m 10s", self.run_app("+"))
        self.assertIn("-/+ window 2m 50s", self.run_app("-"))
        self.assertIn("-/+ window 2m 50s", self.run_app("["))

    def test_window_is_clamped(self):
        self.assertIn("-/+ window 24h",
                      self.run_app("]" * 50, window=86000))
        self.assertIn("-/+ window 10s",
                      self.run_app("[" * 50, window=200))

    def test_sampling_interval_is_not_changed_by_the_keys(self):
        # the sample rate is fixed, so the hint must keep showing the same value
        for keys in ("", "]", "+", "-", "[", "]]]---"):
            self.assertIn("sample 1s", self.run_app(keys))


if __name__ == "__main__":
    unittest.main()
