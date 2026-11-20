#!/usr/bin/env python3
"""Start nvtemp.py in a big terminal, then shrink it while it runs.

Catches the crash that appears when a window is resized down mid-session, not
only when the program is started small.

Usage: python3 tests/resize_pty.py START_ROWS START_COLS END_ROWS END_COLS
"""
import fcntl
import os
import pty
import select
import struct
import sys
import termios
import time

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "nvtemp.py")


def set_size(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def drain(fd, seconds):
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
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
    rows0 = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    cols0 = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    rows1 = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    cols1 = int(sys.argv[4]) if len(sys.argv) > 4 else 50

    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["PATH"] = os.path.join(HERE, "fake_smi") + ":" + env.get("PATH", "")
        env["FAKE_GPUS"] = os.environ.get("FAKE_GPUS", "2")
        os.execvpe(sys.executable, [sys.executable, APP], env)

    set_size(fd, rows0, cols0)
    chunks = [drain(fd, 1.0)]
    set_size(fd, rows1, cols1)
    chunks.append(drain(fd, 1.5))
    os.write(fd, b"q")
    chunks.append(drain(fd, 0.5))
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    out = b"".join(chunks).decode("utf-8", "replace")
    crashed = "Traceback (most recent call last)" in out
    sys.stdout.write(out if crashed else "")
    print(f"resize {rows0}x{cols0} -> {rows1}x{cols1}: crashed={crashed}")
    sys.exit(1 if crashed else 0)


if __name__ == "__main__":
    main()
