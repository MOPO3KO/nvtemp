#!/usr/bin/env bash
# Sweep the single-card view in a real terminal emulator (tmux).
#
# Starts nvtemp.py with the fake nvidia-smi at each terminal size, presses 'n'
# to cycle the selected card, then captures the whole screen with
# `tmux capture-pane` (a real terminal emulator, not a raw byte stream, which
# curses rewrites only partially and therefore cannot be scraped reliably).
# Reports a traceback, a missing hint row, or a missing upper summary block.
set -u
cd "$(dirname "$0")/.."
APP_DIR=$PWD
PY=${PY:-python3}

runs=0
problems=0
for gpus in 1 2 3 5; do
  for rows in 8 9 10 11 12 13 14 16 20 24 30 40; do
    for cols in 46 60 90 200; do
      sock="nvtemp_$$_${gpus}_${rows}_${cols}"
      tmux -L "$sock" new-session -d -s t -x "$cols" -y "$rows" \
        "FAKE_GPUS=$gpus FAKE_TEMP=78 PATH=$APP_DIR/tests/fake_smi:\$PATH $PY $APP_DIR/nvtemp.py" \
        2>/dev/null
      sleep 1.0
      # a single 'n' always switches to the single-card view; four presses
      # would cycle back to the all-cards view, which has no summary block
      tmux -L "$sock" send-keys -t t n
      sleep 1.0
      screen=$(tmux -L "$sock" capture-pane -p -t t 2>/dev/null || true)
      tmux -L "$sock" send-keys -t t q
      sleep 0.2
      tmux -L "$sock" kill-server 2>/dev/null

      runs=$((runs + 1))
      if grep -q -e Traceback -e IndexError <<<"$screen"; then
        problems=$((problems + 1))
        echo "CRASH gpus=$gpus rows=$rows cols=$cols"
        grep -E 'File |Error' <<<"$screen" | tail -4
      elif [[ "$rows" -ge 10 ]] && ! grep -q "q quit" <<<"$screen"; then
        problems=$((problems + 1))
        echo "FOOTER MISSING gpus=$gpus rows=$rows cols=$cols"
      elif [[ "$rows" -ge 14 ]] && ! grep -q "TEMP" <<<"$screen"; then
        problems=$((problems + 1))
        echo "SUMMARY MISSING gpus=$gpus rows=$rows cols=$cols"
      fi
    done
  done
done
echo "runs=$runs problems=$problems"
