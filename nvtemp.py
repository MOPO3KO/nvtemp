#!/usr/bin/env python3
"""
nvtemp.py — console GPU temperature monitor styled after nvtop.

Reproduces nvtop's on-screen design:
  * per-GPU device panels with a name line, colored TEMP values
    (green/yellow/red by slowdown threshold) and [ ||| ]-style percentage
    meters for GPU / MEM temperature;
  * a step "line" plot per GPU drawn with the same ACS characters nvtop uses,
    each GPU in its own color, with the live "GPUx temp(c)" legend overlaid on
    the plot;
  * a bottom status line with keyboard shortcuts, like nvtop's shortcut window.

Colors follow nvtop's interface.c: cyan for field labels, green/yellow/red for
temperature severity, and a per-GPU palette for the plots.
"""
import argparse
import curses
import subprocess
import time
from collections import deque

# ---- color pair ids (nvtop: cyan label, green/yellow/red severity) ----
PAIR_CYAN = 1
PAIR_GREEN = 2
PAIR_YELLOW = 3
PAIR_RED = 4
PAIR_DIM = 5
# plot series colors: same pair for every GPU card
PAIR_CORE = 6   # GPU core temperature line  (cyan)
PAIR_MEM = 7    # GPU memory temperature line (green)
PAIR_BRIGHTRED = 8  # bright scarlet (A_BOLD red) for meters >= 80

# ---- sampling interval: fixed, not user adjustable ----
INTERVAL_DEFAULT = 1.0       # seconds between samples, as before

# ---- visible time window: what the user adjusts with -/+ ----
WINDOW_DEFAULT = 180         # seconds of history shown on the plot
WINDOW_STEP = 10             # seconds added or removed by one [-]/[+] press
WINDOW_MIN = 10              # shortest visible span
WINDOW_MAX = 24 * 3600       # longest visible span: one day

# ---- widths of the trailing columns in the compact summary rows ----
# Each width is the exact printable length of its column, so the layout can be
# computed before drawing anything.
COL_TEMP = len("TEMP ") + 3 + len(chr(176) + "C")   # "TEMP 78°C"
COL_MEM = len("MEM ") + 3 + len(chr(176) + "C")     # "MEM 36°C"
COL_GPU = len("GPU ") + 3 + 1                       # "GPU 45%"
COL_VRAM = len("VRAM ") + 5 + 1 + 5                 # "VRAM 12.3G/24.0G"


def parse_float(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def run_nvidia(extra):
    cmd = ["nvidia-smi"] + extra
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, timeout=5)
        return p.stdout
    except Exception:
        return ""


def get_gpu_names():
    names = {}
    out = run_nvidia(["--query-gpu=index,name", "--format=csv,noheader"])
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",", 1)]
        if len(parts) == 2 and parts[0].isdigit():
            names[int(parts[0])] = parts[1]
    return names


def get_slowdown_thresholds():
    """Try to read per-GPU temperature slowdown threshold (TJmax-ish)."""
    thr = {}
    out = run_nvidia([
        "--query-gpu=index,temperature.gpu,temperature.slow_threshold",
        "--format=csv,noheader,nounits"
    ])
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3 and parts[0].isdigit():
            v = parse_float(parts[2])
            idx = int(parts[0])
            if v is not None and v > 0:
                thr[idx] = v
    return thr


def query_all():
    """Return one tuple per GPU: (index, core_temp, mem_temp, util, used, total).

    Core load and VRAM used/total are requested in the same call to stay at one
    nvidia-smi invocation per sample. If a driver rejects those extra fields the
    query is retried with temperatures only, so a partial driver degrades to
    fewer columns instead of blanking the whole monitor.
    """
    rows = _parse_rows(run_nvidia([
        "--query-gpu=index,temperature.gpu,temperature.memory,"
        "utilization.gpu,memory.used,memory.total",
        "--format=csv,noheader,nounits"]), want_extra=True)
    if rows:
        return rows
    return _parse_rows(run_nvidia([
        "--query-gpu=index,temperature.gpu,temperature.memory",
        "--format=csv,noheader,nounits"]), want_extra=False)


def _parse_rows(out, want_extra):
    rows = []
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        idx = int(parts[0])
        g = parse_float(parts[1])
        m = parse_float(parts[2])
        if want_extra:
            rows.append((idx, g, m, _column(parts, 3), _column(parts, 4),
                         _column(parts, 5)))
        else:
            rows.append((idx, g, m, None, None, None))
    return rows


def _column(parts, i):
    """Parse CSV column i, returning None when the column is absent or N/A."""
    return parse_float(parts[i]) if i < len(parts) else None


def safe_addstr(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        if -x >= len(s):
            return
        s = s[-x:]
        x = 0
    max_len = w - x - 1
    if max_len <= 0:
        return
    try:
        win.addnstr(y, x, s, max_len, attr)
    except curses.error:
        pass


def step_value(value, step, min_value, max_value):
    """Add `step` to `value` and clamp into [min_value, max_value]."""
    return max(min_value, min(max_value, value + step))


def format_span(seconds):
    """Human-readable length of time: '45s', '3m 20s', '1h 30m', '23h 59m'.

    Used for the sampling interval and the visible window, both of which can now
    reach a full day, where plain seconds ('86400s') would be unreadable.
    """
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if s:
        return f"{h}h {m}m {s}s"
    return f"{h}h {m}m" if m else f"{h}h"


def format_bytes_mib(mib):
    """Format a MiB value as GiB with one decimal: 24576 -> '24.0G'."""
    if mib is None:
        return "N/A"
    return f"{mib / 1024:.1f}G"


def temp_pair(temp, slowdown):
    """nvtop draw_temp_color: green < slowdown-5, yellow within 5, red above."""
    if slowdown > 0 and temp is not None:
        if temp >= slowdown:
            return PAIR_RED
        if temp >= slowdown - 5:
            return PAIR_YELLOW
    return PAIR_GREEN


def meter_pair(temp):
    """Fixed-threshold color for meters: green <65, yellow 65-75, red 75-80, bright scarlet >=80."""
    if temp is None:
        return PAIR_GREEN
    if temp >= 80:
        return PAIR_BRIGHTRED
    if temp >= 75:
        return PAIR_RED
    if temp >= 65:
        return PAIR_YELLOW
    return PAIR_GREEN


def load_pair(percent):
    """Color for a GPU core load reading: green <60, yellow 60-85, red >=85."""
    if percent is None:
        return PAIR_GREEN
    if percent >= 85:
        return PAIR_RED
    if percent >= 60:
        return PAIR_YELLOW
    return PAIR_GREEN


BRIGHT_PAIRS = {PAIR_BRIGHTRED}


def value_to_row(value, maxv, rows):
    """Map a temperature onto a data row: 0 = hottest (top), rows-1 = 0 C.

    Clamps into [0, rows-1] for every input, so a caller can never receive an
    index outside the grid even when `rows` is tiny or the value is negative.
    """
    if rows <= 1:
        return 0
    if maxv <= 0:
        return rows - 1
    v = max(0.0, min(value, maxv))
    return max(0, min(rows - 1, int(round((maxv - v) * (rows - 1) / maxv))))


def row_to_value(row, maxv, rows):
    """Inverse of value_to_row, used to print the y-axis labels."""
    if rows <= 1 or maxv <= 0:
        return maxv
    return maxv * (1 - row / (rows - 1))


def build_trace(cols, maxv, rows):
    """Turn sampled values into nvtop-style box-drawing characters.

    Returns a grid of `rows` lists (one per data row, top first), each holding
    len(cols) cells with '─', '│', '┌', '┐', '└', '┘' or None. Rows may be as
    small as 1: every row index comes from value_to_row, which is clamped to the
    grid, so a short terminal cannot index past the end.
    """
    grid = [[None] * len(cols) for _ in range(max(0, rows))]
    if rows <= 0:
        return grid
    py = None
    for x, v in enumerate(cols):
        if v is None:
            continue
        y = value_to_row(v, maxv, rows)
        if py is None:
            grid[y][x] = '─'
            py = y
            continue
        if y == py:
            grid[y][x] = '─'
        elif y > py:   # dropping on screen (temp fell)
            # corner: horizontal from left turns down  -> '┐'
            # bottom: vertical comes up, turns right -> '└'
            grid[py][x] = '┐'
            grid[y][x] = '└'
            for r in range(py + 1, y):
                grid[r][x] = '│'
        else:            # rising on screen (temp rose)
            # corner: horizontal from left turns up  -> '┘'
            # bottom: vertical comes up, turns right -> '┌'
            grid[py][x] = '┘'
            grid[y][x] = '┌'
            for r in range(y + 1, py):
                grid[r][x] = '│'
        py = y
    return grid


def draw_meter(win, y, x, end_x, label, value, maxval, fmt, pair_value=PAIR_GREEN):
    """nvtop-style percentage meter: LABEL[||||    ]value, filled in green."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    end_x = min(end_x, w - 1)
    label = label + " "
    safe_addstr(win, y, x, label, curses.color_pair(PAIR_CYAN) | curses.A_BOLD)
    body_x = x + len(label)
    right_text = " " + fmt
    avail = end_x - body_x
    if avail <= 0:
        return
    right_w = len(right_text)
    if right_w > avail:
        right_text = right_text[-avail:]
        right_w = avail
    bar_len = avail - right_w - 1  # minus closing ']'
    frac = 0.0
    if maxval > 0 and value is not None:
        frac = max(0.0, min(1.0, value / maxval))
    fill = int(round(bar_len * frac))
    attr = curses.A_BOLD if pair_value in BRIGHT_PAIRS else 0
    safe_addstr(win, y, body_x, "[", curses.color_pair(PAIR_DIM))
    if fill > 0:
        safe_addstr(win, y, body_x + 1, "|" * fill,
                    curses.color_pair(pair_value) | attr)
    if bar_len - fill > 0:
        safe_addstr(win, y, body_x + 1 + fill, " " * (bar_len - fill),
                    curses.color_pair(PAIR_DIM))
    safe_addstr(win, y, body_x + 1 + bar_len, "]", curses.color_pair(PAIR_DIM))
    safe_addstr(win, y, body_x + 2 + bar_len, right_text,
                curses.color_pair(pair_value) | attr)


def summary_layout(all_indices, names, width):
    """Column layout shared by every compact summary row.

    Returns (name_width, widths): the name block is as wide as the longest
    "Device N [name]" label of the cards on screen, so TEMP/MEM/GPU/VRAM start
    at the same column for every card. Anchoring the numbers to the right edge
    instead made the gap grow with the window width and left values scattered
    far from their card name.

    Trailing columns are dropped whole when the window is too narrow: TEMP is
    kept first, then MEM, GPU load, VRAM used/total.
    """
    name_width = 0
    for idx in all_indices:
        name_width = max(name_width,
                         len(f"Device {idx} [{names.get(idx, 'N/A')}]"))
    name_width += 2   # gap between the name and the first number

    widths = [COL_TEMP, COL_MEM, COL_GPU, COL_VRAM]
    count = 1
    while count < len(widths):
        need = name_width + sum(widths[:count + 1]) + 2 * count
        if need >= width:            # >= : the last column can't be written
            break
        count += 1

    # A very narrow window: shrink the name block so TEMP still shows.
    if name_width + widths[0] >= width:
        name_width = max(0, width - 1 - widths[0])
    return name_width, widths[:count]


def draw_summary_row(win, row, idx, name_s, g, m, thr, name_width, widths,
                     util=None, used=None, total=None, selected=False):
    """One compact line with a card's live values:

        >Device 0 [Tesla V100]   TEMP  78°C  MEM  36°C  GPU  45%  VRAM 12.3G/24.0G

    The numbers start at `name_width` (sized to the longest name on screen) and
    each column is fixed-width, so values line up across cards; `widths` lists
    which columns fit the current window.
    """
    h, w = win.getmaxyx()
    if row < 0 or row >= h - 1 or w < 24:   # keep the last row for the footer
        return

    marker = ">" if selected else " "
    title = f"{marker}Device {idx}"
    title_pair = PAIR_CORE if selected else PAIR_CYAN
    safe_addstr(win, row, 0, title,
                curses.color_pair(title_pair) | curses.A_BOLD)

    name_x = len(title) + 1
    name_avail = name_width - name_x - 1
    if name_avail >= 2:
        label = f"[{name_s}]"
        if len(label) > name_avail:
            label = label[:name_avail - 1] + "]"
        safe_addstr(win, row, name_x, label, curses.A_BOLD)

    bold = curses.A_BOLD
    deg = chr(176) + "C"
    cyan = curses.color_pair(PAIR_CYAN) | bold
    x = name_width
    cols = ["TEMP", "MEM", "GPU", "VRAM"]
    for ci, cw in enumerate(widths):
        if ci:
            x += 2
        name = cols[ci]
        if name == "TEMP":
            v = f"{g:.0f}" if g is not None else "N/A"
            safe_addstr(win, row, x, "TEMP ", cyan)
            safe_addstr(win, row, x + 5, f"{v:>3}",
                        curses.color_pair(temp_pair(g, thr)) | bold)
            safe_addstr(win, row, x + 8, deg, bold)
        elif name == "MEM":
            v = f"{m:.0f}" if m is not None else "N/A"
            safe_addstr(win, row, x, "MEM ", cyan)
            safe_addstr(win, row, x + 4, f"{v:>3}",
                        curses.color_pair(temp_pair(m, thr)) | bold)
            safe_addstr(win, row, x + 7, deg, bold)
        elif name == "GPU":
            v = f"{util:.0f}" if util is not None else "N/A"
            safe_addstr(win, row, x, "GPU ", cyan)
            safe_addstr(win, row, x + 4, f"{v:>3}",
                        curses.color_pair(load_pair(util)) | bold)
            safe_addstr(win, row, x + 7, "%", bold)
        else:
            u = format_bytes_mib(used)
            t = format_bytes_mib(total)
            safe_addstr(win, row, x, "VRAM ", cyan)
            safe_addstr(win, row, x + 5, f"{u:>5}", bold)
            safe_addstr(win, row, x + 10, "/", bold)
            safe_addstr(win, row, x + 11, f"{t:>5}", bold)
        x += cw


def build_footer(window, slowdown, focus_txt, interval, width):
    """Assemble the bottom hint row so it fits the window width.

    Segments are tried in priority order and the less important tail is dropped
    whole instead of being cut mid-word (a cut "s/S sl" reads as noise). The
    visible-window length is always kept: it is the value the -/+ keys change.
    """
    parts = [
        "q quit",
        f"-/+ window {format_span(window)} ([ ])",
        f"s/S slowdown {slowdown}{chr(176)}C",
        f"n card ({focus_txt})",
        f"sample {format_span(interval)}",
    ]
    # Progressive fallback for very narrow windows: keep the window value but
    # drop its key hint, and shorten the first segment.
    if len(parts[0]) + 3 + len(parts[1]) >= width:
        parts[0] = "q"
        parts[1] = f"-/+ win {format_span(window)}"
    # keep the first two segments, then add the rest while they fit
    keep = 2
    while keep < len(parts):
        candidate = " | ".join(parts[:keep + 1])
        if len(candidate) >= width:      # >= : the last column can't be written
            break
        keep += 1
    return " | ".join(parts[:keep])


def main(stdscr, args):
    curses.curs_set(0)
    stdscr.nodelay(True)

    has_color = curses.has_colors()
    if has_color:
        try:
            curses.start_color()
            curses.use_default_colors()
            bg = -1
            curses.init_pair(PAIR_CYAN, curses.COLOR_CYAN, bg)
            curses.init_pair(PAIR_GREEN, curses.COLOR_GREEN, bg)
            curses.init_pair(PAIR_YELLOW, curses.COLOR_YELLOW, bg)
            curses.init_pair(PAIR_RED, curses.COLOR_RED, bg)
            curses.init_pair(PAIR_DIM, curses.COLOR_WHITE, bg)
            curses.init_pair(PAIR_CORE, curses.COLOR_CYAN, bg)
            curses.init_pair(PAIR_MEM, curses.COLOR_GREEN, bg)
            curses.init_pair(PAIR_BRIGHTRED, curses.COLOR_RED, bg)
        except Exception:
            has_color = False

    window = args.window
    interval = args.interval
    default_slowdown = args.slowdown
    # History is kept for the widest window the program may reach, so growing
    # the window with -/+ reveals data already collected instead of an empty
    # left half. At the default 1 s sampling and a one-day window this is 86400
    # tuples of six floats per card: a few megabytes, acceptable for a monitor.
    history_limit = WINDOW_MAX + 10

    names = get_gpu_names()
    slow = get_slowdown_thresholds()
    data = {}
    next_sample = 0.0

    # which cards to render at once; None = all, else a single index
    focus = None

    while True:
        now = time.time()

        if now >= next_sample:
            for row in query_all():
                idx = row[0]
                if idx not in data:
                    data[idx] = deque()
                # store everything we know about the card so the summary rows can
                # show load and VRAM without asking the driver again
                data[idx].append((now,) + tuple(row[1:]))
            next_sample = now + interval

            # drop only what is older than the widest possible window; the
            # visible window is applied later, when columns are built
            limit = now - history_limit
            for dq in data.values():
                while dq and dq[0][0] < limit:
                    dq.popleft()

        h, w = stdscr.getmaxyx()
        stdscr.erase()

        all_indices = sorted(set(data.keys()) | set(names.keys()))
        if not all_indices or h < 10 or w < 46:
            safe_addstr(stdscr, 0, 0, "No GPUs found or terminal too small")
            stdscr.refresh()
            time.sleep(0.03)
            ch = stdscr.getch()
            if ch == ord('q'):
                break
            continue

        # focused view: one big panel for the selected card; the summary block
        # above lists every card (selected one included) so its parameters are
        # always readable in the same columns as the others.
        summary = all_indices if focus is not None else []
        indices = [focus] if focus is not None else all_indices
        summary_top = 0   # the summary block starts on the first row
        panel_top = len(summary) + 1 if summary else 0

        footer_row = h - 1
        avail_h = h - 1 - 1  # leave bottom for the hint row
        start_time = now - window

        # One shared column layout: numbers start right after the longest card
        # name on screen, so values sit next to their names instead of drifting
        # to the opposite edge of a wide window.
        name_width, col_widths = summary_layout(summary, names, w)

        # compact one-line summary per card, selected card marked with '>'
        for bi, hi in enumerate(summary):
            dhq = data.get(hi, deque())
            last = dhq[-1] if dhq else None
            hlg = last[1] if last else None
            hlm = last[2] if last else None
            hutil = last[3] if last else None
            hused = last[4] if last else None
            htotal = last[5] if last else None
            hthr = slow.get(hi, default_slowdown)
            draw_summary_row(stdscr, summary_top + bi, hi,
                             names.get(hi, "N/A"), hlg, hlm, hthr,
                             name_width, col_widths,
                             util=hutil, used=hused, total=htotal,
                             selected=(hi == focus))

        n = len(indices)
        # nvtop device panel: 2 fixed rows (name, meters) [+ temps row only in
        # single-card view] + plot area + 1 row for the time-axis labels.
        # Give the plot the remaining space.
        temp_row = 1 if focus is not None else 0
        eff_avail = avail_h - (panel_top - 1)
        plot_h = max(3, (eff_avail - n * (3 + temp_row)) // n)
        panel_h = (3 + temp_row) + plot_h

        for pi, idx in enumerate(indices):
            top = panel_top + pi * panel_h
            bottom = min(top + panel_h, footer_row)
            ph = bottom - top
            if ph < 4:
                break

            dq = data.get(idx, deque())
            lg = dq[-1][1] if dq else None
            lm = dq[-1][2] if dq else None
            thr = slow.get(idx, default_slowdown)

            # ---- line 0: Device name (cyan); mark the selected card ----
            selected = (idx == focus)
            if selected:
                title = f">Device {idx}"
            else:
                title = f" Device {idx}"
            safe_addstr(stdscr, top, 0, title,
                       curses.color_pair(PAIR_CYAN) | curses.A_BOLD)
            name_s = names.get(idx, "N/A")
            safe_addstr(stdscr, top, len(title) + 1, f"[{name_s}]",
                       curses.A_BOLD)
            if selected:
                # draw the selection marker separately so only the card is highlighted
                safe_addstr(stdscr, top, 0, ">",
                           curses.color_pair(PAIR_CORE) | curses.A_BOLD)

            # the TEMP/MEM values line is kept only in single-card view to save
            # space when several devices are shown together
            temp_row = 1 if focus is not None else 0

            if temp_row:
                # ---- line 1: TEMP value + MEM temp, nvtop draw_temp_color ----
                trow = top + 1
                gval = f"{lg:.0f}" if lg is not None else "N/A"
                mval = f"{lm:.0f}" if lm is not None else "N/A"
                safe_addstr(stdscr, trow, 0, "TEMP",
                           curses.color_pair(PAIR_CYAN) | curses.A_BOLD)
                safe_addstr(stdscr, trow, 5, gval.rjust(3),
                           curses.color_pair(temp_pair(lg, thr)) | curses.A_BOLD)
                safe_addstr(stdscr, trow, 8, chr(176) + "C", curses.A_BOLD)
                safe_addstr(stdscr, trow, 11, "MEM",
                           curses.color_pair(PAIR_CYAN) | curses.A_BOLD)
                safe_addstr(stdscr, trow, 15, mval.rjust(3),
                           curses.color_pair(temp_pair(lm, thr)) | curses.A_BOLD)
                safe_addstr(stdscr, trow, 18, chr(176) + "C", curses.A_BOLD)
            else:
                gval = f"{lg:.0f}" if lg is not None else "N/A"
                mval = f"{lm:.0f}" if lm is not None else "N/A"

            # ---- line 1/2: GPU / MEM temperature meters (one per half) ----
            mrow = top + 1 + temp_row
            half = max(14, (w - 2) // 2)
            draw_meter(stdscr, mrow, 0, half, "GPU", lg, thr,
                      f"{gval}{chr(176)}C", meter_pair(lg))
            draw_meter(stdscr, mrow, half, w - 1, "MEM", lm, thr,
                      f"{mval}{chr(176)}C", meter_pair(lm))

            # ---- plot area: nvtop-style step line ----
            chart_top = top + 2 + temp_row
            chart_bottom = bottom - 2  # last panel row holds time labels
            chart_h = chart_bottom - chart_top
            left = 4
            # one column each for the left and right rails of the frame
            plot_w = w - left - 2
            if chart_h < 1 or plot_w < 8:
                continue

            # same colors on every card: cyan = GPU core, green = GPU memory
            color_pair = PAIR_CORE
            mem_color = PAIR_MEM

            # y scale: labels on left, 0 at bottom, max = slowdown (like 100%).
            # The top row (chart_top) is reserved for the frame border, so the
            # temperature line gets every interior row of the frame, from the
            # first row below the top border down to chart_bottom.
            data_rows = chart_h

            # The window is anchored to the newest sample, not to the clock:
            # between samples "now" keeps moving, so a clock-anchored window made
            # the last drawn point slide left away from the right edge (the
            # "disappearing tail"). Anchoring to the newest sample keeps the line
            # touching the right edge at all times.
            newest = dq[-1][0] if dq else now
            end_time = max(newest, start_time + 1)
            view_span = max(end_time - start_time, 1)

            def time_to_x(t):
                x = int((t - start_time) * (plot_w - 1) / view_span)
                return max(0, min(plot_w - 1, x))

            # y-axis labels: for a single card place a label on every chart row
            # (no gaps), otherwise sparse so labels don't collide.
            if len(indices) == 1 and thr > 0 and data_rows > 0:
                for r in range(data_rows):
                    lbl = int(round(row_to_value(r, thr, data_rows)))
                    safe_addstr(stdscr, chart_top + 1 + r, 0,
                               str(lbl).rjust(left - 1),
                               curses.color_pair(PAIR_DIM))
            elif thr > 0:
                nice = [1, 2, 5, 10, 15, 20, 25, 30, 45, 60, 90]
                divisions = max(2, min(int(chart_h / 3), 10))
                step = next((s for s in nice if thr / s <= divisions), thr)
                for lbl in range(0, int(thr) + 1, max(1, int(step))):
                    safe_addstr(stdscr,
                               chart_top + 1 + value_to_row(lbl, thr, data_rows),
                               0, str(lbl).rjust(left - 1),
                               curses.color_pair(PAIR_DIM))
            # build column data (max per column) on the 0..thr scale
            cols_g = [None] * plot_w
            cols_m = [None] * plot_w
            for sample in dq:
                t, g, m = sample[0], sample[1], sample[2]
                if t < start_time:
                    continue
                x = time_to_x(t)
                if g is not None:
                    cols_g[x] = g if cols_g[x] is None else max(cols_g[x], g)
                if m is not None:
                    cols_m[x] = m if cols_m[x] is None else max(cols_m[x], m)

            def interp(cols):
                last = None
                for i in range(plot_w):
                    if cols[i] is not None:
                        if last is not None and i - last > 1:
                            for j in range(last + 1, i):
                                f = (j - last) / (i - last)
                                cols[j] = cols[last] * (1 - f) + cols[i] * f
                        last = i
                return cols

            interp(cols_g)
            interp(cols_m)

            # nvtop-style polyline: horizontal '─' runs at a constant level,
            # '│' vertical jumps, and ┌┐└┘ corners where the line turns.
            # Data rows sit directly under the top border and fill every row
            # down to chart_bottom, so a short terminal still draws something.
            gp = build_trace(cols_g, thr, data_rows)
            mp = build_trace(cols_m, thr, data_rows)

            # render MEM first (behind), then GPU (front)
            for r in range(data_rows):
                for x in range(plot_w):
                    mc = mp[r][x]
                    if mc is not None:
                        safe_addstr(stdscr, chart_top + 1 + r, left + x, mc,
                                   curses.color_pair(mem_color) | curses.A_BOLD)
                    gc = gp[r][x]
                    if gc is not None:
                        safe_addstr(stdscr, chart_top + 1 + r, left + x, gc,
                                   curses.color_pair(color_pair) | curses.A_BOLD)

            # ---- frame around the plot: labels stay outside the box ----
            # left rail at left-1 (y-axis numbers are to its left), a closing
            # right rail at w-1, and top/bottom borders connecting the two.
            frame_l = left - 1
            frame_r = w - 2  # w-1 is the last column, which safe_addstr can't draw
            safe_addstr(stdscr, chart_top, frame_l,
                       "┌" + "─" * plot_w + "┐", curses.color_pair(PAIR_DIM))
            for y in range(chart_top + 1, chart_bottom + 1):
                safe_addstr(stdscr, y, frame_l, "│", curses.color_pair(PAIR_DIM))
                safe_addstr(stdscr, y, frame_r, "│", curses.color_pair(PAIR_DIM))
            cap = f"last {format_span(window)}"
            pad = max(0, plot_w - 1 - len(cap))
            safe_addstr(stdscr, chart_bottom + 1, frame_l,
                       "└" + "─" + cap + "─" * pad + "┘",
                       curses.color_pair(PAIR_DIM))

            # nvtop legend overlaid at the top-left of the plot with the live value
            legend = f"GPU{idx} temp(c) {gval}"
            safe_addstr(stdscr, chart_top, left, " " * max(0, min(len(legend) + 1, plot_w)),
                       curses.color_pair(PAIR_DIM))
            safe_addstr(stdscr, chart_top, left, legend,
                       curses.color_pair(color_pair) | curses.A_BOLD)
            if lm is not None:
                leg2 = f"{mval}(mem)"
                safe_addstr(stdscr, chart_top, left + len(legend) + 1, leg2,
                           curses.color_pair(mem_color) | curses.A_BOLD)

        # ---- footer (nvtop shortcut window) ----
        # The visible-window length is shown next to its keys: with only
        # "[ ] interval" the user could not tell which key changes it or what it
        # is set to. The sampling interval is fixed and shown for reference.
        focus_txt = "all" if focus is None else f"GPU{focus}"
        safe_addstr(stdscr, footer_row, 0,
                   build_footer(window, default_slowdown, focus_txt,
                                interval, w),
                   curses.color_pair(PAIR_DIM))

        stdscr.refresh()
        time.sleep(0.03)
        ch = stdscr.getch()

        if ch == ord('q'):
            break
        elif ch == ord('n'):
            # cycle: all -> 0 -> 1 -> ... -> all
            if focus is None:
                focus = all_indices[0] if all_indices else None
            else:
                cur = all_indices.index(focus)
                focus = all_indices[cur + 1] if cur + 1 < len(all_indices) else None
        elif ch in (ord(']'), ord('+')):
            # the keys adjust the visible span, not the sampling rate
            window = step_value(window, WINDOW_STEP, WINDOW_MIN, WINDOW_MAX)
        elif ch in (ord('['), ord('-')):
            window = step_value(window, -WINDOW_STEP, WINDOW_MIN, WINDOW_MAX)
        elif ch == ord('s'):
            default_slowdown = min(150, default_slowdown + 5)
        elif ch == ord('S'):
            default_slowdown = max(50, default_slowdown - 5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Per-GPU console temperature monitor, nvtop-styled"
    )
    parser.add_argument("--interval", type=float, default=INTERVAL_DEFAULT,
                      help=f"Sampling interval in seconds (default "
                           f"{INTERVAL_DEFAULT:.0f}). This is fixed while "
                           f"running; -/+ adjust the visible window instead.")
    parser.add_argument("--window", type=int, default=WINDOW_DEFAULT,
                      help=f"Visible time window in seconds (default "
                           f"{WINDOW_DEFAULT}); -/+ change it by "
                           f"{WINDOW_STEP}s while running, up to "
                           f"{WINDOW_MAX / 3600:.0f}h")
    parser.add_argument("--slowdown", type=int, default=90,
                      help="Temperature slowdown threshold in C (plot max, color "
                           "severity). Driver value used when available.")
    args = parser.parse_args()

    try:
        curses.wrapper(main, args)
    except KeyboardInterrupt:
        pass
