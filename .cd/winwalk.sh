#!/usr/bin/env bash
# winwalk.sh <app.exe> <out> [phases]: builder CD's Cap Notes persona walk on Windows (git bash).
# Scratch profiles and synthetic meetings only. Nothing reaches a Cap server.
#   a11y     every Notes surface (en): UI Automation tree (what Narrator reads) + 14 real Tab presses
#   locales  en/de/ja/hi light + en dark, one shot per surface
#   slow     notes window on 2 logical cores (process affinity): time to the first listed library
#            and to a meeting page, 5 rounds
set -u
APP=$1; OUT=$2; PHASES=${3:-"a11y locales slow"}
HERE=$(cd "$(dirname "$0")" && pwd)
W_HERE=$(cygpath -w "$HERE")
mkdir -p "$OUT/shots" "$OUT/a11y" "$OUT/work"
grab() { powershell -NoProfile -File "$W_HERE\\grab.ps1" -Out "$(cygpath -w "$1")" > /dev/null 2>&1; }
uia() { powershell -NoProfile -ExecutionPolicy Bypass -File "$W_HERE\\uia.ps1" "$@"; }
P=""; WPID=""

base_data() { # <dir> <theme>
  mkdir -p "$1/meetings" "$1/rec" "$1/logs"
  printf '%s' "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"theme\":\"$2\"}}" > "$1/store"
  printf '%s' '{"enabled": true, "setupCompleted": true}' > "$1/meetings/settings.json"
}

launch() { # <dir> <locale> <log> [env...]
  local D=$1 L=$2 LOG=$3; shift 3
  env CAP_GPUI_APP_DATA_DIR="$(cygpath -w "$D")" CAP_GPUI_RECORDINGS_DIR="$(cygpath -w "$D/rec")" \
    CAP_GPUI_LOG_DIR="$(cygpath -w "$D/logs")" CAP_GPUI_TAURI_STORE="$(cygpath -w "$D/store")" \
    CAP_GPUI_NO_TRAY=1 CAP_GPUI_LOCALE="$L" RUST_LOG=warn,cap_gpui=info \
    HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 "$@" \
    "$APP" < /dev/null > "$LOG" 2>&1 &
  P=$!
  sleep 1
  WPID=$(cat /proc/$P/winpid 2>/dev/null)
}

stop() {
  [ -n "$WPID" ] && taskkill //F //PID "$WPID" > /dev/null 2>&1
  [ -n "$P" ] && { kill "$P" 2>/dev/null; wait "$P" 2>/dev/null; }
  P=""; WPID=""; sleep 1
}

wait_line() { local i=0; while [ $i -lt $(( $3 * 5 )) ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 0.2; i=$((i + 1)); done; return 1; }

SEEDED="$OUT/work/seeded"
seed() {
  [ -d "$SEEDED/documents" ] && return
  base_data "$SEEDED" light
  launch "$SEEDED" en "$OUT/work/seed.log" CAP_GPUI_DOCS_SEED=40 CAP_GPUI_DOCS_OPEN=1 \
    CAP_GPUI_DOCS_CORPUS="$(cygpath -w "$HERE/m17-hinglish.txt")|$(cygpath -w "$HERE/m17-hinglish.md")"
  wait_line "$OUT/work/seed.log" "DOCS READY" 120
  stop
}
copy_seed() { mkdir -p "$1"; cp -R "$SEEDED/." "$1/"; base_data "$1" "$2"; }

SURFACES="main|main
docs-home|docs:home
docs-meeting|docs:kind:meeting
docs-mixed|docs:select:Payments
docs-live|docs:kind:meeting,live
docs-ask|docs:kind:meeting,ask
docs-empty|empty
setup-welcome|setup:welcome
setup-calendar|setup:calendar
settings|settings
prompt|live:prompt-scheduled
indicator|live:recording"

open_surface() { # <name> <how> <locale> <theme> <dir> [extra env...]
  local name=$1 how=$2 L=$3 T=$4 D=$5 LOG="$5/app.log"; shift 5
  case "$how" in
    docs:*) copy_seed "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1 CAP_GPUI_DOCS_STATE="${how#docs:}" "$@"
      wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    empty) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1; wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    setup:*) base_data "$D" "$T"; printf '%s' '{"enabled": false, "setupCompleted": false}' > "$D/meetings/settings.json"
      launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_MEETING_SETUP="${how#setup:}"; sleep 8 ;;
    settings) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_SETTINGS=meetings; sleep 8 ;;
    live:*) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_MEETING_UI="${how#live:}"; sleep 8 ;;
    main) base_data "$D" "$T"; launch "$D" "$L" "$LOG"; sleep 8 ;;
  esac
}

phase_a11y() {
  local line name how D
  while IFS= read -r line; do
    name=${line%%|*}; how=${line#*|}
    D="$OUT/work/a11y-$name"; mkdir -p "$D"
    open_surface "$name" "$how" en light "$D"
    uia front "$WPID"
    grab "$OUT/shots/a11y-$name-0.png"
    uia tree "$WPID" "$(cygpath -w "$OUT/a11y/$name-tree.txt")"
    uia tab "$WPID" "$(cygpath -w "$OUT/a11y/$name-tab1.txt")" 3
    grab "$OUT/shots/a11y-$name-tab3.png"
    uia tab "$WPID" "$(cygpath -w "$OUT/a11y/$name-tab2.txt")" 11
    grab "$OUT/shots/a11y-$name-tab14.png"
    stop
    echo "a11y $name: $(tail -1 "$OUT/a11y/$name-tree.txt")"
  done <<< "$SURFACES"
}

phase_locales() {
  local v L T line name how D
  for v in en-light de-light ja-light hi-light en-dark; do
    L=${v%-*}; T=${v##*-}
    while IFS= read -r line; do
      name=${line%%|*}; how=${line#*|}
      D="$OUT/work/loc-$v-$name"; mkdir -p "$D"
      open_surface "$name" "$how" "$L" "$T" "$D"
      grab "$OUT/shots/loc-$name--$v.png"
      stop
      rm -rf "$D/documents"
    done <<< "$SURFACES"
  done
}

phase_slow() {
  # Two logical cores for the whole app (affinity mask 0x3), CPU-only drawing like a GPU-less laptop.
  local r D LOG t0 t1
  for r in 1 2 3 4 5; do
    D="$OUT/work/slow-$r"; copy_seed "$D" light; LOG="$D/app.log"
    t0=$(date +%s%3N)
    launch "$D" en "$LOG" CAP_GPUI_DOCS_OPEN=1 CAP_GPUI_DOCS_STATE="kind:meeting"
    powershell -NoProfile -Command "(Get-Process -Id $WPID).ProcessorAffinity = 3" 2>&1 | head -2
    wait_line "$LOG" "DOCS READY" 90
    t1=$(date +%s%3N)
    echo "slow round $r: launch to meeting page ready $((t1 - t0)) ms (affinity 0x3, cores=$(nproc))" | tee -a "$OUT/a11y/slow.txt"
    [ $r = 1 ] && grab "$OUT/shots/slow-meeting.png"
    stop
  done
}

seed
for ph in $PHASES; do echo "== $ph $(date +%T)"; "phase_$ph"; done
grep -ahE "panicked|ERROR" "$OUT"/work/*/app.log 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | sort | uniq -c | sort -rn | head -30 > "$OUT/errors.txt"
cat "$OUT/errors.txt"
