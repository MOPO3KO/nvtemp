#!/usr/bin/env python3
"""Run nvtemp.py inside a pseudo-terminal of a given size and report crashes.

Usage: python3 tests/repro_pty.py ROWS COLS [seconds]

Uses the fake nvidia-smi from tests/fake_smi so no GPU is needed. Prints the
child's output and exits non-zero when the run produced a traceback or a
non-zero exit code.
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


def main():
    rows = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    cols = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

    pid, fd = pty.fork()
    if pid == 0:
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env["PATH"] = os.path.join(HERE, "fake_smi") + ":" + env.get("PATH", "")
        env["FAKE_GPUS"] = os.environ.get("FAKE_GPUS", "1")
        env["FAKE_TEMP"] = os.environ.get("FAKE_TEMP", "30")
        env["FAKE_MEM"] = os.environ.get("FAKE_MEM", "36")
        os.execvpe(sys.executable, [sys.executable, APP], env)

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    chunks = []
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
            chunks.append(data)
    os.write(fd, b"q")
    time.sleep(0.3)
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 0.1)
            if not r:
                break
            data = os.read(fd, 65536)
            if not data:
                break
            chunks.append(data)
    except OSError:
        pass
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError:
        status = 0

    out = b"".join(chunks).decode("utf-8", "replace")
    crashed = "Traceback (most recent call last)" in out
    sys.stdout.write(out)
    sys.stdout.write("\n--- exit_status=%d crashed=%s ---\n" % (status, crashed))
    sys.exit(1 if crashed else 0)


if __name__ == "__main__":
    main()
