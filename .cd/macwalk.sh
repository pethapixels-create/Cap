#!/bin/bash
# macwalk.sh <stage dir> <out>: builder CD's Cap Notes walk on the Mac mini's console session.
# Per surface (en, light): AX tree (what VoiceOver reads), then 14 real Tab presses with the
# focused element after each and a few screenshots; then de/ja/hi light + en dark shots.
# Keys are pressed only while our own app is frontmost. Scratch profiles, synthetic meetings.
set -u
S=$1; OUT=$2
BIN=$S/bin/cap-gpui
HERE=$S/cd
AX=$OUT/cdax
mkdir -p "$OUT/shots" "$OUT/a11y" "$OUT/work"
swiftc -O "$HERE/cdax.swift" -o "$AX" 2>&1 | tail -3
echo "os $(sw_vers -productVersion) $(uname -m) ax-trusted=$("$AX" trusted)" | tee "$OUT/facts.txt"
P=""
shot() { screencapture -x "$1" 2>/dev/null || echo "screencapture failed $1" >> "$OUT/facts.txt"; }

base_data() {
  mkdir -p "$1/meetings" "$1/rec" "$1/logs" "$1/home"
  printf '%s' "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"theme\":\"$2\"}}" > "$1/store"
  printf '%s' '{"enabled": true, "setupCompleted": true}' > "$1/meetings/settings.json"
}
launch() { # <dir> <locale> <log> [env...]
  local D=$1 L=$2 LOG=$3; shift 3
  env CAP_GPUI_APP_DATA_DIR="$D" CAP_GPUI_RECORDINGS_DIR="$D/rec" CAP_GPUI_LOG_DIR="$D/logs" \
    CAP_GPUI_TAURI_STORE="$D/store" CAP_GPUI_NO_TRAY=1 CAP_GPUI_LOCALE="$L" RUST_LOG=warn,cap_gpui=info \
    HTTPS_PROXY=http://127.0.0.1:9 HTTP_PROXY=http://127.0.0.1:9 "$@" "$BIN" < /dev/null > "$LOG" 2>&1 &
  P=$!
}
stop() { [ -n "$P" ] && { kill "$P" 2>/dev/null; sleep 1.2; kill -9 "$P" 2>/dev/null; wait "$P" 2>/dev/null; }; P=""; }
wait_line() { local i=0; while [ $i -lt $(( $3 * 5 )) ]; do grep -qaE "$2" "$1" 2>/dev/null && return 0; sleep 0.2; i=$((i + 1)); done; return 1; }

SEEDED="$OUT/work/seeded"
base_data "$SEEDED" light
launch "$SEEDED" en "$OUT/work/seed.log" CAP_GPUI_DOCS_SEED=40 CAP_GPUI_DOCS_OPEN=1 \
  CAP_GPUI_DOCS_CORPUS="$HERE/m17-hinglish.txt|$HERE/m17-hinglish.md"
wait_line "$OUT/work/seed.log" "DOCS READY" 120; stop
copy_seed() { mkdir -p "$1"; cp -R "$SEEDED/." "$1/"; base_data "$1" "$2"; }

SURFACES="main|main
docs-home|docs:home
docs-meeting|docs:kind:meeting
docs-live|docs:kind:meeting,live
docs-ask|docs:kind:meeting,ask
docs-empty|empty
setup-welcome|setup:welcome
setup-permissions|setup:permissions
settings|settings
prompt|live:prompt-scheduled
indicator|live:recording"

open_surface() { # <how> <locale> <theme> <dir>
  local how=$1 L=$2 T=$3 D=$4 LOG="$4/app.log"
  case "$how" in
    docs:*) copy_seed "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1 CAP_GPUI_DOCS_STATE="${how#docs:}"
      wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    empty) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_DOCS_OPEN=1; wait_line "$LOG" "DOCS READY" 60; sleep 2 ;;
    setup:*) base_data "$D" "$T"; printf '%s' '{"enabled": false, "setupCompleted": false}' > "$D/meetings/settings.json"
      launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_MEETING_SETUP="${how#setup:}"; sleep 7 ;;
    settings) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_AUTO_SETTINGS=meetings; sleep 7 ;;
    live:*) base_data "$D" "$T"; launch "$D" "$L" "$LOG" CAP_GPUI_MEETING_UI="${how#live:}"; sleep 7 ;;
    main) base_data "$D" "$T"; launch "$D" "$L" "$LOG"; sleep 7 ;;
  esac
}

while IFS= read -r line; do
  name=${line%%|*}; how=${line#*|}
  D="$OUT/work/a11y-$name"; mkdir -p "$D"
  open_surface "$how" en light "$D"
  front=$("$AX" front "$P")
  shot "$OUT/shots/a11y-$name-0.png"
  "$AX" tree "$P" > "$OUT/a11y/$name-tree.txt" 2>&1
  : > "$OUT/a11y/$name-tab.txt"
  for k in $(seq 1 14); do
    front=$("$AX" front "$P")
    if [ "$front" != "$P" ]; then echo "tab $k: not frontmost (front=$front), skipped" >> "$OUT/a11y/$name-tab.txt"; continue; fi
    "$AX" key tab; sleep 0.6
    printf 'tab %02d: %s\n' "$k" "$("$AX" focus "$P" 2>&1 | head -1)" >> "$OUT/a11y/$name-tab.txt"
    case $k in 1|3|8|14) shot "$OUT/shots/a11y-$name-tab$k.png";; esac
  done
  stop
  echo "a11y $name: $(tail -1 "$OUT/a11y/$name-tree.txt")" | tee -a "$OUT/facts.txt"
done <<< "$SURFACES"

for v in de-light ja-light hi-light en-dark; do
  L=${v%-*}; T=${v##*-}
  for line in "docs-home|docs:home" "docs-meeting|docs:kind:meeting" "setup-welcome|setup:welcome" "prompt|live:prompt-scheduled" "indicator|live:recording" "settings|settings"; do
    name=${line%%|*}; how=${line#*|}
    D="$OUT/work/loc-$v-$name"; mkdir -p "$D"
    open_surface "$how" "$L" "$T" "$D"
    "$AX" front "$P" > /dev/null
    shot "$OUT/shots/loc-$name--$v.png"
    stop
  done
done
find "$OUT/work" -name documents -type d -prune -exec rm -r {} + 2>/dev/null
grep -ahE "panicked|ERROR" "$OUT"/work/*/app.log 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' | sort | uniq -c | sort -rn | head -20 > "$OUT/errors.txt"
echo "done $(date +%T)" >> "$OUT/facts.txt"
