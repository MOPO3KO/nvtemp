#!/usr/bin/env python3
"""Tests for the footer hint, the interval/slowdown keys and the span format.

The keys are handled inline in main(), so these tests drive a fake curses
session: a window object that records drawing, and a key queue that feeds the
main loop. That way the real bindings and the real clamping are exercised.

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


class SpanFormatTest(unittest.TestCase):
    def setUp(self):
        self.nvtemp = load_app()

    def test_short_spans_stay_in_seconds(self):
        self.assertEqual(self.nvtemp.format_span(45), "45s")
        self.assertEqual(self.nvtemp.format_span(10), "10s")

    def test_minutes(self):
        self.assertEqual(self.nvtemp.format_span(180), "3m")
        self.assertEqual(self.nvtemp.format_span(200), "3m 20s")

    def test_hours(self):
        self.assertEqual(self.nvtemp.format_span(3600), "1h")
        self.assertEqual(self.nvtemp.format_span(5400), "1h 30m")
        self.assertEqual(self.nvtemp.format_span(86400), "24h")

    def test_vram_format(self):
        self.assertEqual(self.nvtemp.format_bytes_mib(24576), "24.0G")
        self.assertEqual(self.nvtemp.format_bytes_mib(12288), "12.0G")
        self.assertEqual(self.nvtemp.format_bytes_mib(None), "N/A")


class FooterTest(unittest.TestCase):
    def setUp(self):
        self.nvtemp = load_app()

    def test_window_always_visible_with_its_keys(self):
        for width in (46, 60, 90, 200):
            line = self.nvtemp.build_footer(180, 90, "all", 1, width)
            self.assertIn("window 3m ([ ])", line)
            self.assertIn("-/+", line)

    def test_long_window_is_readable(self):
        line = self.nvtemp.build_footer(86400, 90, "all", 1, 200)
        self.assertIn("window 24h", line)
        self.assertNotIn("86400", line)

    def test_sample_rate_is_reported_separately(self):
        line = self.nvtemp.build_footer(180, 90, "all", 1, 200)
        self.assertIn("sample 1s", line)

    def test_never_exceeds_the_window_width(self):
        for width in range(24, 200):
            line = self.nvtemp.build_footer(180, 90, "all", 1, width)
            self.assertLess(len(line), width,
                            f"width={width} line={line!r}")

    def test_drops_whole_segments_not_half_words(self):
        for width in (46, 60, 70, 90):
            line = self.nvtemp.build_footer(180, 90, "all", 1, width)
            self.assertFalse(line.endswith(" |"), line)
            for seg in line.split(" | "):
                self.assertTrue(seg.strip(), line)
            self.assertNotRegex(line, r"\| [a-zA-Z/]*$")

    def test_window_value_is_updated_in_the_hint(self):
        self.assertIn("window 4m 10s",
                      self.nvtemp.build_footer(250, 90, "GPU1", 1, 200))


class KeyHandlingTest(unittest.TestCase):
    """Drive main() with a scripted key queue and check the state it applies."""

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

    def run_app(self, keys, interval=1.0, rows=24, cols=90):
        """Run main() through a scripted key sequence; return the last footer."""
        win = self.FakeWin(rows, cols)
        win.keys = [ord(k) for k in keys] + [ord('q')]
        nv = self.nvtemp

        class Args:
            pass

        args = Args()
        args.window = 180
        args.interval = interval
        args.slowdown = 90

        nv.time.sleep = lambda s: None
        nv.main(win, args)
        return win.line(rows - 1)

    def test_slowdown_keys_change_the_threshold(self):
        self.assertIn("slowdown 95", self.run_app("s"))
        self.assertIn("slowdown 85", self.run_app("S"))
        self.assertIn("slowdown 150", self.run_app("s" * 30))
        self.assertIn("slowdown 50", self.run_app("S" * 30))

    def test_card_key_switches_the_view(self):
        footer = self.run_app("n")
        self.assertIn("n card (GPU0)", footer)
        # pressing 'n' again with a single card returns to the all-cards view
        self.assertIn("n card (all)", self.run_app("nn"))

    def test_window_length_is_shown_once_as_a_span(self):
        footer = self.run_app("")
        self.assertIn("window 3m", footer)
        self.assertNotIn("last 180s", footer)


class QueryParsingTest(unittest.TestCase):
    """query_all() must carry load and VRAM through, and fall back gracefully."""

    def setUp(self):
        self.nvtemp = load_app()

    def test_extra_fields_are_parsed(self):
        nv = self.nvtemp
        nv.run_nvidia = lambda cmd: (
            "0, 55, 61, 92, 20000, 24576\n" if "utilization.gpu" in " ".join(cmd)
            else "")
        self.assertEqual(nv.query_all(),
                         [(0, 55.0, 61.0, 92.0, 20000.0, 24576.0)])

    def test_falls_back_when_the_driver_rejects_extra_fields(self):
        nv = self.nvtemp
        calls = []

        def fake_run(cmd):
            calls.append(cmd)
            if any("utilization.gpu" in c for c in cmd):
                return ""            # driver refused the extra fields
            return "0, 55, 61\n"

        nv.run_nvidia = fake_run
        rows = nv.query_all()
        self.assertEqual(rows, [(0, 55.0, 61.0, None, None, None)])
        self.assertEqual(len(calls), 2, "must retry once with temperatures only")

    def test_na_values_become_none_instead_of_crashing(self):
        nv = self.nvtemp
        nv.run_nvidia = lambda cmd: "0, 55, 61, [N/A], [N/A], 24576\n"
        self.assertEqual(nv.query_all(),
                         [(0, 55.0, 61.0, None, None, 24576.0)])


if __name__ == "__main__":
    unittest.main()
