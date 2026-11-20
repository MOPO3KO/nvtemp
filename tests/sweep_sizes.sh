#!/usr/bin/env bash
# Sweep nvtemp.py over terminal sizes and card counts, report any crash.
# Uses tests/repro_pty.py with the fake nvidia-smi, so no GPU is required.
set -u
cd "$(dirname "$0")"

runs=0
crashes=0
for gpus in 1 2 3 5; do
  for rows in 8 9 10 11 12 13 14 16 20 24 30 40; do
    for cols in 46 60 80 200; do
      out=$(FAKE_GPUS=$gpus python3 tests/repro_pty.py "$rows" "$cols" 0.6 2>&1 || true)
      runs=$((runs + 1))
      if grep -q -e Traceback -e IndexError <<<"$out"; then
        crashes=$((crashes + 1))
        echo "CRASH gpus=$gpus rows=$rows cols=$cols"
        grep -E 'File |Error|line ' <<<"$out" | tail -6
      fi
    done
  done
done
echo "runs=$runs crashes=$crashes"
