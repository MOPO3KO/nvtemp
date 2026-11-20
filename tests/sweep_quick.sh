#!/usr/bin/env bash
# Quick sweep of terminal sizes at a short sampling interval.
#
# The point is only to catch crashes and layout problems, so the sampling
# interval stays at the default 1 s and each run is a couple of seconds long.
# Long intervals are not exercised here: they would make every run slow without
# testing anything extra.
#
# Usage: bash tests/sweep_quick.sh [ROWS...] [COLS...]
set -u
cd "$(dirname "$0")/.."
APP_DIR=$PWD

rows_list=${1:-"10 14 20"}
cols_list=${2:-"46 100"}
runs=0
problems=0

for gpus in 1 3; do
  for rows in $rows_list; do
    for cols in $cols_list; do
      sock="quick_$$_${gpus}_${rows}_${cols}"
      tmux -L "$sock" new-session -d -s t -x "$cols" -y "$rows" \
        "FAKE_GPUS=$gpus FAKE_TEMP=72 PATH=$APP_DIR/tests/fake_smi:\$PATH python3 $APP_DIR/nvtemp.py" \
        2>/dev/null
      sleep 1.5
      screen=$(tmux -L "$sock" capture-pane -p -t t 2>/dev/null || true)
      tmux -L "$sock" send-keys -t t n
      sleep 1.0
      single=$(tmux -L "$sock" capture-pane -p -t t 2>/dev/null || true)
      tmux -L "$sock" kill-server 2>/dev/null

      runs=$((runs + 2))
      for screen_text in "$screen" "$single"; do
        if grep -q -e Traceback -e IndexError <<<"$screen_text"; then
          problems=$((problems + 1))
          echo "CRASH gpus=$gpus rows=$rows cols=$cols"
          grep -E 'File |Error' <<<"$screen_text" | tail -4
        elif [[ "$rows" -ge 10 ]] && ! grep -q "q quit" <<<"$screen_text"; then
          problems=$((problems + 1))
          echo "FOOTER MISSING gpus=$gpus rows=$rows cols=$cols"
        fi
      done
    done
  done
done
echo "runs=$runs problems=$problems"
