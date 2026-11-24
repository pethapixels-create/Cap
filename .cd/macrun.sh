#!/bin/zsh
# CD mini hold: waits (max 2 h) for both GUI locks and >= 10 GB free, takes both as CD, runs
# macwalk.sh (<= 25 min watchdog kills the whole tree and releases both locks), then releases.
# Run as rmini in the console session. Usage: macrun.sh <stage dir> <out dir> [walk script]
set -u
S=$1; OUT=$2; WALK=${3:-$S/cd/macwalk.sh}
L1=/Users/Shared/cap-gui.lock
L2=/Volumes/CapGPUIBuild/locks/gui
LOG=$OUT/../run.log
mkdir -p "$OUT"
mine() { [ "$(cat "$1/owner" 2>/dev/null)" = CD ]; }
stop_tree() {
  pkill -f "$WALK"
  pkill -f "$S/bin/cap-gpui"
  pkill -f "$OUT/cdax"
}
release() {
  stop_tree
  mine $L1 && find $L1 -delete
  mine $L2 && find $L2 -delete
  echo "$(date +%T) released" >> $LOG
}
take() {
  mkdir $L1 2>/dev/null || return 1
  echo CD > $L1/owner; chmod -R 777 $L1
  if mkdir $L2 2>/dev/null; then echo CD > $L2/owner; chmod -R 777 $L2; return 0; fi
  find $L1 -delete; return 1
}
free_gb() { df -g /Volumes/CapGPUIBuild | awk 'NR==2{print $4}'; }
echo "$(date +%T) waiting for both locks (free $(free_gb) GB)" >> $LOG
n=0
until [ "$(free_gb)" -ge 10 ] && take; do
  n=$((n + 1)); [ $n -gt 240 ] && { echo "$(date +%T) gave up waiting" >> $LOG; exit 3; }
  sleep 30
done
trap release EXIT INT TERM
echo "$(date +%T) holding both locks" >> $LOG
sleep 20
( sleep 1500; echo "$(date +%T) watchdog fired" >> $LOG; stop_tree; kill $$ ) &
WD=$!
bash "$WALK" "$S" "$OUT" >> $LOG 2>&1
kill $WD 2>/dev/null
echo "$(date +%T) walk done, $(du -sh $OUT | cut -f1) in out" >> $LOG
