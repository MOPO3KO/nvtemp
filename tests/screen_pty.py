#!/usr/bin/env python3
"""Render nvtemp.py's output through a terminal emulator and print the screen.

Usage:
    python3 tests/screen_pty.py ROWS COLS "keys"

Requires the `pyte` package (pip install pyte) plus the fake nvidia-smi in
tests/fake_smi; no GPU is needed. The keys string is sent one character at a
time, then the final screen is printed as plain text, so the layout can be
inspected exactly as a user would see it.
"""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

import pyte

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "nvtemp.py")


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(fd, seconds):
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            out.append(data)
    return b"".join(out)


def main():
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    cols = int(sys.argv[2]) if len(sys.argv) > 2 else 90
    keys = sys.argv[3] if len(sys.argv) > 3 else ""
    seconds = float(sys.argv[4]) if len(sys.argv) > 4 else 1.2

    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)

    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ)
        env["TERM"] = "xterm"
        env["PATH"] = os.path.join(HERE, "fake_smi") + ":" + env.get("PATH", "")
        os.execvpe(sys.executable, [sys.executable, APP], env)

    set_size(fd, rows, cols)
    stream.feed(drain(fd, seconds).decode("utf-8", "replace"))
    for k in keys:
        os.write(fd, k.encode())
        stream.feed(drain(fd, 0.5).decode("utf-8", "replace"))
    os.write(fd, b"q")
    drain(fd, 0.3)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    for line in screen.display:
        print(line.rstrip())


if __name__ == "__main__":
    main()
