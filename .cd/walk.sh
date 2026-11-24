#!/usr/bin/env bash
# walk.sh <app> <out> [phases]: builder CD's Cap Notes persona walk on Linux (Xvfb + openbox).
# Scratch profiles and synthetic meetings only. Nothing reaches a Cap server.
# Phases (default all): a11y locales scale monitors
#   a11y      every Notes surface (en): AT-SPI tree (what Orca reads), then 14 real Tab presses
#             (xdotool) with a screenshot and the focused node after each
#   locales   the same surfaces in 7 locales (light) and en/de dark, one shot each
#   scale     notes home + meeting page at Xft.dpi 144 and 192 (text scaling / HiDPI)
#   monitors  two monitors side by side: prompt, indicator, notes window placement
set -u
APP=$1; OUT=$2; PHASES=${3:-"a11y locales scale monitors"}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$OUT/shots" "$OUT/a11y" "$OUT/work"
grab() { import -window root "$1" 2>/dev/null; }
P=""

base_data() { # <dir> <theme>
  mkdir -p "$1/meetings" "$1/rec" "$1/logs" "$1/home"
  printf '%s' "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"theme\":\"$2\"}}" > "$1/store"
  printf '%s' '{"enabled": true, "setupCompleted": true}' > "$1/meetings/settings.json"
}

launch() { # <dir> <locale> <log> [env...]
  local D=$1 L=$2 LOG=$3; shift 3
  env HOME="$D/home" CAP_GPUI_APP_DATA_DIR="$D" CAP_GPUI_RECORDINGS_DIR="$D/rec" CAP_GPUI_LOG_DIR="$D/logs" \
    CAP_GPUI_TAURI_STORE="$D/store" CAP_GPUI_NO_TRAY=1 CAP_GPUI_LOCALE="$L" RUST_LOG=warn,cap_gpui=info \
    HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 "$@" \
    "$APP" < /dev/null > "$LOG" 2>&1 &
  P=$!
}

stop() { [ -n "$P" ] && { kill "$P" 2>/dev/null; sleep 1.2; kill -9 "$P" 2>/dev/null; wait "$P" 2>/dev/null; }; P=""; }

wait_line() { # <file> <regex> <seconds>
  local i=0
  while [ $i -lt $(( $3 * 5 )) ]; do
    grep -qaE "$2" "$1" 2>/dev/null && return 0
    sleep 0.2; i=$((i + 1))
  done
  return 1
}

SEEDED="$OUT/work/seeded"
seed() { # one library for every run: 40 synthetic notes + the Hinglish meeting on top
  [ -d "$SEEDED/documents" ] && return
  base_data "$SEEDED" light
  launch "$SEEDED" en "$OUT/work/seed.log" CAP_GPUI_DOCS_SEED=40 CAP_GPUI_DOCS_OPEN=1 \
    CAP_GPUI_DOCS_CORPUS="$HERE/m17-hinglish.txt|$HERE/m17-hinglish.md"
  wait_line "$OUT/work/seed.log" "DOCS READY" 120
  stop
}

copy_seed() { # <dir> <theme>
  mkdir -p "$1"; cp -R "$SEEDED/." "$1/"; base_data "$1" "$2"
}

# surface name | how to reach it. docs:* = notes window hook states; others are their own hooks.
SURFACES="main|main
docs-home|docs:home
docs-meeting|docs:kind:meeting
docs-mixed|docs:select:Payments
docs-transcript|docs:kind:meeting,page_transcript
docs-live|docs:kind:meeting,live
docs-ask|docs:kind:meeting,ask
docs-offline|docs:kind:meeting,error:offline
docs-search|docs:search:budget
docs-empty|empty
setup-welcome|setup:welcome
setup-permissions|setup:permissions
setup-calendar|setup:calendar
settings|settings
prompt|live:prompt-scheduled
indicator|live:recording"

open_surface() { # <name> <how> <locale> <theme> <dir>; leaves the app running in $P
  local name=$1 how=$2 L=$3 T=$4 D=$5 LOG="$5/app.log"
  case "$how" in
    docs:*)
      copy_seed "$D" "$T"
      launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1 CAP_GPUI_DOCS_STATE="${how#docs:}"
      wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    empty)
      base_data "$D" "$T"
      launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1
      wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    setup:*)
      base_data "$D" "$T"
      printf '%s' '{"enabled": false, "setupCompleted": false}' > "$D/meetings/settings.json"
      launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_MEETING_SETUP="${how#setup:}"
      sleep 7 ;;
    settings)
      base_data "$D" "$T"
      printf '%s' '{"sources":[{"id":"ics-cd","name":"Team","config":{"kind":"ics","url":"https://calendar.example.com/team.ics"},"status":{}}]}' > "$D/meetings/calendars.json"
      launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_SETTINGS=meetings
      sleep 7 ;;
    live:*)
      base_data "$D" "$T"
      launch "$D" "$L" "$LOG" CAP_GPUI_MEETING_UI="${how#live:}"
      sleep 7 ;;
    main)
      base_data "$D" "$T"
      launch "$D" "$L" "$LOG"
      sleep 7 ;;
  esac
}

focus_window() { # brings the app's largest visible window to the front
  local best="" area=0 w g ww hh
  for w in $(xdotool search --onlyvisible --pid "$P" 2>/dev/null); do
    g=$(xdotool getwindowgeometry "$w" 2>/dev/null | awk '/Geometry/{print $2}')
    ww=${g%x*}; hh=${g#*x}
    [ -n "$ww" ] && [ $((ww * hh)) -gt $area ] && { area=$((ww * hh)); best=$w; }
  done
  [ -n "$best" ] && xdotool windowactivate --sync "$best" 2>/dev/null
  echo "$best"
}

phase_a11y() {
  local line name how D W
  while IFS= read -r line; do
    name=${line%%|*}; how=${line#*|}
    D="$OUT/work/a11y-$name"; mkdir -p "$D"
    open_surface "$name" "$how" en light "$D"
    W=$(focus_window); sleep 0.8
    grab "$OUT/shots/a11y-$name-0.png"
    python3 "$HERE/atspi.py" tree "$P" > "$OUT/a11y/$name-tree.txt" 2>&1
    : > "$OUT/a11y/$name-tab.txt"
    for k in $(seq 1 14); do
      [ -n "$W" ] && xdotool key --window "$W" Tab 2>/dev/null || xdotool key Tab
      sleep 0.6
      printf 'tab %02d: %s\n' "$k" "$(python3 "$HERE/atspi.py" focus "$P" 2>&1 | head -2 | tr '\n' ' ')" >> "$OUT/a11y/$name-tab.txt"
      case $k in 1|2|3|5|8|14) grab "$OUT/shots/a11y-$name-tab$k.png";; esac
    done
    stop
    echo "a11y $name: $(tail -1 "$OUT/a11y/$name-tree.txt")"
  done <<< "$SURFACES"
}

phase_locales() {
  local L T line name how D
  for v in en-light de-light es-light fr-light hi-light ja-light zh-CN-light en-dark de-dark; do
    L=${v%-*}; T=${v##*-}
    while IFS= read -r line; do
      name=${line%%|*}; how=${line#*|}
      case "$name" in docs-search|docs-offline|setup-permissions) [ "$L" != en ] && [ "$L" != de ] && [ "$L" != ja ] && continue;; esac
      D="$OUT/work/loc-$v-$name"; mkdir -p "$D"
      open_surface "$name" "$how" "$L" "$T" "$D"
      grab "$OUT/shots/loc-$name--$v.png"
      stop
      find "$D" -mindepth 1 -maxdepth 1 -name documents -exec rm -rf {} + 2>/dev/null
    done <<< "$SURFACES"
  done
}

phase_scale() {
  local dpi L name how D
  for dpi in 144 192; do
    echo "Xft.dpi: $dpi" | xrdb -merge
    for L in en de; do
      for line in "docs-home|docs:home" "docs-meeting|docs:kind:meeting" "setup-welcome|setup:welcome"; do
        name=${line%%|*}; how=${line#*|}
        D="$OUT/work/scale-$dpi-$L-$name"; mkdir -p "$D"
        open_surface "$name" "$how" "$L" light "$D"
        grab "$OUT/shots/scale$dpi-$name--$L.png"
        stop
      done
    done
  done
  echo "Xft.dpi: 96" | xrdb -merge
}

phase_monitors() {
  # Two 1600x1000 monitors side by side on one 3200x1000 X screen (RandR 1.5 monitors).
  xrandr --setmonitor CD-LEFT 1600/423x1000/265+0+0 screen 2>&1 | head -2
  xrandr --setmonitor CD-RIGHT 1600/423x1000/265+1600+0 none 2>&1 | head -2
  xrandr --listmonitors > "$OUT/a11y/monitors.txt" 2>&1
  local line name how D
  for line in "prompt|live:prompt-scheduled" "indicator|live:recording" "docs-home|docs:home" "main|main"; do
    name=${line%%|*}; how=${line#*|}
    D="$OUT/work/mon-$name"; mkdir -p "$D"
    open_surface "$name" "$how" en light "$D"
    grab "$OUT/shots/mon-$name.png"
    for w in $(xdotool search --onlyvisible --pid "$P" 2>/dev/null); do
      echo "$name $(xdotool getwindowname "$w" 2>/dev/null) $(xdotool getwindowgeometry "$w" 2>/dev/null | tr '\n' ' ')" >> "$OUT/a11y/monitors.txt"
    done
    stop
  done
}

seed
for ph in $PHASES; do echo "== $ph $(date +%T)"; "phase_$ph"; done
grep -ahE "panicked|ERROR" "$OUT"/work/*/app.log 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | sort | uniq -c | sort -rn | head -30 > "$OUT/errors.txt"
cat "$OUT/errors.txt"
