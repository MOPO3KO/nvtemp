#!/usr/bin/env python3
"""Drive nvtemp.py in a pseudo-terminal: render, send keys, capture the screen.

Usage:
    python3 tests/drive_pty.py ROWS COLS "keys" [seconds]

Example (switch to single-card view, then capture):
    python3 tests/drive_pty.py 24 90 n 1.5

The keys string is sent one character at a time with a short pause, so each
keypress is processed by a separate draw cycle. Uses the fake nvidia-smi from
tests/fake_smi, so no GPU is required. Exits non-zero if a traceback appears.
"""
import fcntl
import os
import pty
import re
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

    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["PATH"] = os.path.join(HERE, "fake_smi") + ":" + env.get("PATH", "")
        os.execvpe(sys.executable, [sys.executable, APP], env)

    set_size(fd, rows, cols)
    frames = [drain(fd, seconds)]
    for k in keys:
        os.write(fd, k.encode())
        frames.append(drain(fd, 0.5))
    os.write(fd, b"q")
    frames.append(drain(fd, 0.4))
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass

    raw = b"".join(frames).decode("utf-8", "replace")
    if "Traceback (most recent call last)" in raw:
        sys.stdout.write(raw)
        print("crashed=True")
        sys.exit(1)

    last = frames[-2] if len(frames) > 1 else frames[-1]
    text = last.decode("utf-8", "replace")
    # keep only the printable rows of the final frame
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    print(text.replace("\r", ""))
    print("crashed=False")


if __name__ == "__main__":
    main()
