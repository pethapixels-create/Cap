#!/usr/bin/env bash
# bw.sh <cap-gpui binary> <work dir> <group>: Cap Notes hardening runs through the real app on Linux
# (X11), modelled on scripts/ci/meetings/linux.sh and BI's gotcha harness. Nothing reaches a Cap
# server: meeting cases talk to bw_stub.py, calendar cases to a local feed. Each case writes
# <work>/<case>/facts.txt and screenshots.
set -uo pipefail
BIN=$1; WORK=$2; GROUP=$3
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CI=$ROOT/scripts/ci/meetings
FIX=$ROOT/apps/desktop-gpui/tests/fixtures/meetings
MODELS=${CAP_MEETINGS_MODELS:-}
mkdir -p "$WORK"
epoch() { python3 -c 'import time;print(int(time.time()*1000))'; }
shot() { import -window root "$1" 2>/dev/null || true; }
tree_rss_kb() { # resident memory of a process and its children, KB
  local total=0 p
  for p in $1 $(pgrep -P "$1" 2> /dev/null); do
    total=$((total + $(ps -o rss= -p "$p" 2> /dev/null | tr -d ' ' || echo 0)))
  done
  echo $total
}
cpu_s() { ps -o cputimes= -p "$1" 2> /dev/null | tr -d ' ' || echo -1; }

export DISPLAY=:91
Xvfb $DISPLAY -screen 0 1920x1080x24 -nolisten tcp > "$WORK/xvfb.log" 2>&1 &
sleep 1
openbox > "$WORK/openbox.log" 2>&1 &

# Any attempt to open a link or file lands in this log instead of a browser.
SHIM=$WORK/shim; mkdir -p "$SHIM"
for prog in xdg-open gio gnome-open kde-open wslview; do
  printf '#!/bin/sh\necho "$(date +%%s%%3N) %s $*" >> %s/opened.log\nexit 0\n' "$prog" "$WORK" > "$SHIM/$prog"
  chmod +x "$SHIM/$prog"
done
export PATH="$SHIM:$PATH"
touch "$WORK/opened.log"

app_env() { # app_env <run dir>: the environment every launch gets
  echo HOME="$1/home" CAP_GPUI_APP_DATA_DIR="$1/data" CAP_GPUI_NO_TRAY=1 PATH="$PATH" DISPLAY="$DISPLAY" \
    XDG_SESSION_TYPE=x11 LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}" ORT_DYLIB_PATH="${ORT_DYLIB_PATH:-}" \
    DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-}" PULSE_SERVER="${PULSE_SERVER:-}"
}

# launch <run dir> <log> [memory max] [extra env...]: start the app, print its pid. With a memory
# max, it runs in its own cgroup with that much memory and no swap, like a laptop with that much
# free memory.
launch() {
  local R=$1 LOG=$2 MEM=$3; shift 3
  if [ -n "$MEM" ]; then
    sudo systemd-run --scope -q --unit "bw-$(basename "$R")-$RANDOM" -p MemoryMax="$MEM" -p MemorySwapMax=0 -- \
      runuser -u "$(id -un)" -- env $(app_env "$R") RUST_LOG=warn,cap_gpui=info "$@" "$BIN" < /dev/null > "$LOG" 2>&1 &
    local outer=$! p=
    for _ in $(seq 100); do p=$(pgrep -n -f "^$BIN" 2> /dev/null) && [ -n "$p" ] && break; sleep 0.1; done
    echo "${p:-$outer}"
  else
    env $(app_env "$R") RUST_LOG=warn,cap_gpui=info "$@" "$BIN" < /dev/null > "$LOG" 2>&1 &
    echo $!
  fi
}

profile() { # profile <run dir>: a signed-in scratch profile with Notes set up
  local R=$1 ENGINE=${2:-local} PORT=${3:-8899} SCHEME=${4:-http} HOST=${5:-127.0.0.1}
  mkdir -p "$R/home" "$R/data/meetings" "$R/shots"
  if [ -n "$MODELS" ]; then
    local M=$R/home/.local/share/so.cap.desktop/transcription_models
    mkdir -p "$M"; for f in "$MODELS"/*; do ln -sf "$f" "$M/"; done
  fi
  echo "{\"enabled\": true, \"setupCompleted\": true, \"notifications\": {\"scheduled\": true, \"detected\": false, \"leadSecs\": 120}, \"transcription\": {\"engine\": \"$ENGINE\", \"live\": true}}" > "$R/data/meetings/settings.json"
  echo "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"recordingCountdown\":0,${BW_GENERAL_EXTRA:-}\"serverUrl\":\"$SCHEME://$HOST:$PORT\"},\"auth\":{\"secret\":{\"api_key\":\"ci-test-key\"},\"user_id\":\"citestuser0001\",\"plan\":{\"upgraded\":true,\"manual\":false,\"last_checked\":$(date +%s)},\"organizations\":[]}}" > "$R/data/store"
}

# ---------------------------------------------------------------- calendar feeds
# calendar <case> <feed kind> [memory max]: the app subscribed to one ICS feed on loopback. Samples
# memory and CPU for 150 s, then relaunches with the feed gone (the cached copy only) for 60 s.
calendar() {
  local C=$1 KIND=$2 MEM=${3:-}
  local R=$WORK/$C; profile "$R"; mkdir -p "$R/feed"
  python3 "$HERE/bw_feed.py" "$KIND" "$R/feed/feed.ics" > "$R/run.txt"
  (cd "$R/feed" && exec python3 -m http.server 8777 --bind 127.0.0.1 > "$R/feed.log" 2>&1) &
  local FEED=$!
  echo '{"sources":[{"id":"ics-bw","name":"Work","config":{"kind":"ics","url":"http://127.0.0.1:8777/feed.ics"},"status":{}}]}' > "$R/data/meetings/calendars.json"
  local P peak=0 rss t0 died=
  t0=$(epoch)
  P=$(launch "$R" "$R/app.log" "$MEM" CAP_GPUI_LIBRARY_WINDOW=1 CAP_GPUI_AUTO_LIBRARY=home)
  echo "memory_max ${MEM:-none}" >> "$R/run.txt"
  for i in $(seq 150); do
    if ! kill -0 "$P" 2> /dev/null; then died=$(( ($(epoch) - t0) / 1000 )); break; fi
    rss=$(tree_rss_kb "$P"); [ "$rss" -gt "$peak" ] && peak=$rss
    [ $((i % 10)) -eq 0 ] && echo "t=${i}s rss_mb=$((rss / 1024)) cpu=$(cpu_s "$P")" >> "$R/samples.txt"
    [ "$i" -eq 20 ] && shot "$R/shots/at20s.png"
    sleep 1
  done
  shot "$R/shots/end.png"
  {
    echo "first launch: peak_rss_mb=$((peak / 1024)) died_at_s=${died:-no} cpu=$(cpu_s "$P")"
    echo "feed requests: $(grep -c 'GET /feed.ics' "$R/feed.log")"
    echo "cache files: $(find "$R/data/meetings" -maxdepth 2 -type f -printf '%s %P\n' 2> /dev/null | sort -rn | head -4 | tr '\n' ';')"
    echo "oom: $(sudo dmesg 2> /dev/null | grep -ciE 'out of memory|oom-kill|killed process')"
  } >> "$R/run.txt"
  kill "$P" 2> /dev/null; sleep 1; kill -9 "$P" 2> /dev/null
  kill "$FEED" 2> /dev/null
  # Second launch: the feed is gone, only what the first launch cached is read.
  peak=0; died=; t0=$(epoch)
  P=$(launch "$R" "$R/app-relaunch.log" "$MEM" CAP_GPUI_LIBRARY_WINDOW=1 CAP_GPUI_AUTO_LIBRARY=home)
  for i in $(seq 60); do
    if ! kill -0 "$P" 2> /dev/null; then died=$(( ($(epoch) - t0) / 1000 )); break; fi
    rss=$(tree_rss_kb "$P"); [ "$rss" -gt "$peak" ] && peak=$rss
    sleep 1
  done
  shot "$R/shots/relaunch.png"
  echo "relaunch (cache only): peak_rss_mb=$((peak / 1024)) died_at_s=${died:-no} cpu=$(cpu_s "$P")" >> "$R/run.txt"
  kill "$P" 2> /dev/null; sleep 1; kill -9 "$P" 2> /dev/null
  {
    cat "$R/run.txt"; cat "$R/samples.txt" 2> /dev/null
    echo "panics $(cat "$R"/app*.log | grep -c panicked)"
    sed 's/\x1b\[[0-9;]*m//g' "$R"/app*.log | grep -iE "calendar|feed|panicked|memory" | sed -E 's/^[^ ]+ +//' | sort | uniq -c | sort -rn | head -12
  } | tee "$R/facts.txt"
  find "$R/feed" -name '*.ics' -size +1M -delete
}

# hostile_prompt <case> <feed kind>: a meeting 45 s away whose title, people and links are hostile.
# The prompt shows, is accepted (click, Return), its menu is opened, and the notes window shows the
# meeting. Facts: whether anything tried to open a link, what the windows look like.
hostile_prompt() {
  local C=$1 KIND=$2
  local R=$WORK/$C; profile "$R" local; mkdir -p "$R/feed"
  python3 "$HERE/bw_feed.py" "$KIND" "$R/feed/feed.ics" > "$R/run.txt"
  (cd "$R/feed" && exec python3 -m http.server 8777 --bind 127.0.0.1 > "$R/feed.log" 2>&1) &
  local FEED=$!
  echo '{"sources":[{"id":"ics-bw","name":"Work","config":{"kind":"ics","url":"http://127.0.0.1:8777/feed.ics"},"status":{}}]}' > "$R/data/meetings/calendars.json"
  local before_open P win=
  before_open=$(wc -l < "$WORK/opened.log")
  P=$(launch "$R" "$R/app.log" "")
  for _ in $(seq 60); do
    win=$(xdotool search --name '^Cap Meeting Prompt$' 2> /dev/null | head -1)
    [ -n "$win" ] && break
    sleep 1
  done
  sleep 1; shot "$R/shots/prompt.png"
  echo "prompt window: ${win:-none}" >> "$R/run.txt"
  if [ -n "$win" ]; then
    eval "$(xdotool getwindowgeometry --shell "$win")"
    # Every button on the prompt, left to right, then Return.
    for fx in 30 50 62 74 86 94; do
      xdotool mousemove $((X + WIDTH * fx / 100)) $((Y + HEIGHT / 2)) click 1; sleep 1.5
      shot "$R/shots/click-$fx.png"
    done
    xdotool windowfocus --sync "$win" 2> /dev/null; xdotool key Return; sleep 6
  fi
  shot "$R/shots/after.png"
  local notes
  notes=$(xdotool search --name '^Cap Notes$' 2> /dev/null | head -1)
  [ -n "$notes" ] && xdotool windowactivate "$notes" 2> /dev/null && sleep 2 && shot "$R/shots/notes.png"
  {
    echo "alive $(kill -0 "$P" 2> /dev/null && echo yes || echo no)"
    echo "link opens: $(( $(wc -l < "$WORK/opened.log") - before_open ))"
    tail -n +$((before_open + 1)) "$WORK/opened.log" | head -5
    echo "windows: $(xwininfo -root -tree | grep -oE '"Cap [^"]*"' | sort | uniq -c | tr '\n' ';')"
    echo "panics $(grep -c panicked "$R/app.log")"
    sed 's/\x1b\[[0-9;]*m//g' "$R/app.log" | grep -iE "prompt|join|open|panicked" | sed -E 's/^[^ ]+ +//' | head -10
  } >> "$R/run.txt"
  kill "$P" "$FEED" 2> /dev/null; sleep 1; kill -9 "$P" 2> /dev/null
  local DOC
  DOC=$(ls -d "$R/data/documents"/*/ 2> /dev/null | head -1)
  [ -n "$DOC" ] && cp "$DOC/doc.json" "$R/doc.json" 2> /dev/null
  [ -f "$R/doc.json" ] && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print('doc title len',len(d.get('title','')),'repr',repr(d.get('title','')[:120]))" "$R/doc.json" >> "$R/run.txt"
  tee "$R/facts.txt" < "$R/run.txt"
}

# ---------------------------------------------------------------- meetings
audio_facts() { # audio_facts <doc dir> <label>: size and decodable length of each audio file
  local f
  for f in "$1"/audio/*.ogg; do
    [ -f "$f" ] || continue
    echo "$2 $(basename "$f") bytes=$(stat -c %s "$f") probe_s=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2> /dev/null) decode_errors=$(ffmpeg -v error -i "$f" -f null - 2>&1 | wc -l)"
  done
}
audio_setup() {
  MEET=$WORK/meeting; mkdir -p "$MEET"
  ffmpeg -v error -y -i "$FIX/you.opus" -f f32le -ac 1 -ar 48000 "$MEET/mic.f32"
  ffmpeg -v error -y -i "$FIX/others.opus" -ac 1 -ar 48000 -c:a pcm_s16le "$MEET/sys.wav"
  cp "$FIX/truth.json" "$MEET/"
  DUR=$(python3 -c "import json;print(json.load(open('$MEET/truth.json'))['duration_ms']//1000)")
  pulse_dir=$(mktemp -d)
  pulseaudio -n --daemonize=yes --exit-idle-time=-1 \
    -L "module-native-protocol-unix socket=$pulse_dir/native auth-anonymous=1" \
    -L "module-null-sink sink_name=room sink_properties=device.description=Room" \
    -L "module-null-sink sink_name=micsink sink_properties=device.description=MicFeed" \
    -L "module-remap-source master=micsink.monitor source_name=mic source_properties=device.description=Mic"
  export PULSE_SERVER=unix:$pulse_dir/native
  for _ in $(seq 40); do pactl info > /dev/null 2>&1 && break; sleep 0.25; done
  pactl set-default-sink room; pactl set-default-source mic
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK/key.pem" -out "$WORK/cert.pem" -days 2 \
    -subj "/CN=meet.google.com" -addext "subjectAltName=DNS:meet.google.com" 2> /dev/null
  CHROME=$(command -v google-chrome || command -v google-chrome-stable || command -v chromium)
}

start_call() {
  python3 "$CI/call_server.py" "$MEET" 8443 "$1/page_started.log" --tls "$WORK/cert.pem" "$WORK/key.pem" &
  echo $! > "$1/call_server.pid"
  sleep 0.5
  "$CHROME" --user-data-dir="$1/chrome" --no-first-run --no-default-browser-check --disable-gpu \
    --host-resolver-rules="MAP meet.google.com:443 127.0.0.1:8443" --ignore-certificate-errors \
    --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required \
    --window-position=0,560 --window-size=900,500 "https://meet.google.com/abc-defg-hij?at=$2" \
    > "$1/chrome.log" 2>&1 &
  echo $! > "$1/chrome.pid"
}
stop_call() {
  kill "$(cat "$1/chrome.pid")" "$(cat "$1/call_server.pid")" 2> /dev/null || true
  pkill -f "user-data-dir=$1/chrome" || true
}

fill_disk() { dd if=/dev/zero of="$1/.fill" bs=64k 2> /dev/null; sync; echo "filled $(epoch) free_kb=$(df -k "$1" | awk 'NR==2{print $4}')"; }

# meeting <case> <local|cloud> <fault spec>: one scripted meeting through the real app.
# MIDWAY: fill (notes disk fills half way and stays full), fillstop (fills 3 s before Stop),
#   clock (wall clock jumps 2 h forward half way), second (a second Cap starts half way)
# PRE: docfile (the notes folder is a file), docloop (the notes folder is a symlink loop),
#   tmpfs=<size> (the notes folder is a small disk), tls (server is https behind a private CA)
# RELAUNCH=1: after the run, free the disk and start Cap again for 90 s, to see what it recovers.
meeting() {
  local C=$1 ENGINE=$2 SPEC=$3 LINGER=${LINGER:-100}
  local R=$WORK/$C
  local SCHEME=http HOST=127.0.0.1
  if [ "${TLS:-}" = 1 ]; then SCHEME=https; HOST=cap-proxy.test; fi
  profile "$R" "$ENGINE" 8899 "$SCHEME" "$HOST"
  case "${PRE:-}" in
    docfile) echo "not a folder" > "$R/data/documents" ;;
    docloop) ln -s documents "$R/data/documents" ;;
    tmpfs=*) mkdir -p "$R/data/documents"; sudo mount -t tmpfs -o size=${PRE#tmpfs=},uid=$(id -u),gid=$(id -g) tmpfs "$R/data/documents" ;;
  esac
  if [ "${TLS:-}" = 1 ]; then
    BW_TLS="$WORK/tls/server.pem:$WORK/tls/server.key" python3 "$HERE/bw_stub.py" 8899 "$R/stub.jsonl" "$SPEC" &
  else
    python3 "$HERE/bw_stub.py" 8899 "$R/stub.jsonl" "$SPEC" &
  fi
  local STUB=$!
  for _ in $(seq 50); do python3 -c "import socket;socket.create_connection(('127.0.0.1',8899),0.2)" 2> /dev/null && break; sleep 0.1; done
  if [ "${TLS:-}" = 1 ]; then
    echo "curl through the OS trust store: $(curl -s -o /dev/null -w '%{http_code}' https://cap-proxy.test:8899/api/desktop/meetings/capabilities 2>&1 || true)" >> "$R/pre.txt"
  fi
  local SECS=$((DUR + 12))
  local P peak=0 rss
  P=$(launch "$R" "$R/app.log" "" RUST_LOG=warn,cap_gpui=info,cap_meetings=info CAP_GPUI_DOCS_OPEN=live ${EXTRA_ENV:-} \
    CAP_GPUI_AUTO_MEETING=$SECS CAP_GPUI_AUTO_MEETING_LINGER=$LINGER CAP_GPUI_AUTO_MEETING_OUT="$R/summary.json")
  echo "spec $SPEC engine $ENGINE pre ${PRE:-} midway ${MIDWAY:-} tls ${TLS:-0}" > "$R/run.txt"
  for _ in $(seq 900); do grep -qa "CAP_GPUI_AUTO_MEETING: recording" "$R/app.log" && break; kill -0 "$P" 2> /dev/null || break; sleep 0.1; done
  local AT=$(( $(epoch) + 5000 ))
  start_call "$R" "$AT"
  python3 -c "import time;time.sleep(max(0,$AT/1000-time.time()))"
  echo "mic $(epoch)" >> "$R/run.txt"
  pacat --playback --device=micsink --raw --format=float32le --rate=48000 --channels=1 < "$MEET/mic.f32" &
  local MICP=$!
  local LIVE
  case "${MIDWAY:-}" in
    fillstop) sleep $((SECS - 8)) ;;
    *) sleep $((DUR / 2)) ;;
  esac
  shot "$R/shots/recording.png"
  LIVE=$(ls -d "$R/data/documents"/*/ 2> /dev/null | head -1)
  echo "live_doc $LIVE" >> "$R/run.txt"
  case "${MIDWAY:-}" in
    fill|fillstop) fill_disk "$R/data/documents" >> "$R/run.txt"; sleep 3; shot "$R/shots/after-fill.png" ;;
    clock) sudo timedatectl set-ntp false 2> /dev/null; sudo date -s "@$(( $(date +%s) + 7200 ))" > /dev/null; echo "clock +2h $(epoch)" >> "$R/run.txt" ;;
    kill9)
      sudo kill -9 "$P"; echo "killed -9 mid-meeting $(epoch)" >> "$R/run.txt"; sleep 2
      audio_facts "$LIVE" "at kill" >> "$R/run.txt"
      echo "transcript lines at kill: $(wc -l < "$LIVE/transcript.jsonl" 2> /dev/null)" >> "$R/run.txt" ;;
    second)
      local P2
      P2=$(launch "$R" "$R/app-second.log" "")
      sleep 8
      echo "second instance alive after 8 s: $(kill -0 "$P2" 2> /dev/null && echo yes || echo no)" >> "$R/run.txt"
      kill "$P2" 2> /dev/null ;;
  esac
  local drained=
  for i in $(seq $(( (SECS + LINGER + 240) * 5 ))); do
    [ -s "$R/summary.json" ] && break
    kill -0 "$P" 2> /dev/null || break
    if [ $((i % 25)) -eq 0 ]; then rss=$(tree_rss_kb "$P"); [ "$rss" -gt "$peak" ] && peak=$rss; fi
    if [ -z "$drained" ] && grep -qa "CAP_GPUI_AUTO_MEETING: drained" "$R/app.log"; then
      drained=1; echo "drained $(epoch)" >> "$R/run.txt"
      if [ "${KILL_AT_NOTES:-}" = 1 ]; then
        sleep 3; sudo kill -9 "$P"; echo "killed -9 while notes were written $(epoch)" >> "$R/run.txt"
        echo "notes requests at kill: $(grep -c '/meetings/notes' "$R/stub.jsonl" 2> /dev/null)" >> "$R/run.txt"
        break
      fi
      sleep 20; shot "$R/shots/notes-20s.png"
    fi
    sleep 0.2
  done
  shot "$R/shots/end.png"
  echo "alive_at_end $(kill -0 "$P" 2> /dev/null && echo yes || echo no) peak_rss_mb=$((peak / 1024))" >> "$R/run.txt"
  kill "$P" "$MICP" 2> /dev/null; sleep 1; kill -9 "$P" 2> /dev/null
  stop_call "$R"
  if [ "${MIDWAY:-}" = clock ]; then sudo date -s "@$(( $(date +%s) - 7200 ))" > /dev/null; sudo timedatectl set-ntp true 2> /dev/null; fi
  { echo "== documents tree after the run"; find -L "$R/data/documents" -maxdepth 3 -printf '%y %s %P\n' 2> /dev/null | head -40; } >> "$R/run.txt"
  if [ "${RELAUNCH:-}" = 1 ]; then
    find "$R/data/documents" -maxdepth 1 -name .fill -delete
    echo "freed, free_kb=$(df -k "$R/data/documents" | awk 'NR==2{print $4}'), relaunching $(epoch)" >> "$R/run.txt"
    P=$(launch "$R" "$R/app-relaunch.log" "" RUST_LOG=warn,cap_gpui=info,cap_meetings=info CAP_GPUI_DOCS_OPEN=1)
    sleep 90; shot "$R/shots/relaunch.png"
    kill "$P" 2> /dev/null; sleep 1; kill -9 "$P" 2> /dev/null
    sed 's/\x1b\[[0-9;]*m//g' "$R/app-relaunch.log" | grep -iE "recover|repair|notes|unfinished|error|warn|panicked" | sed -E 's/^[^ ]+ +//' | head -16 >> "$R/run.txt"
    [ -n "${LIVE:-}" ] && audio_facts "$LIVE" "after relaunch" >> "$R/run.txt"
    echo "stub after relaunch: $(tail -n 5 "$R/stub.jsonl" 2> /dev/null | tr '\n' ' ' | cut -c1-600)" >> "$R/run.txt"
    { echo "== documents tree after relaunch"; find "$R/data/documents" -maxdepth 3 -printf '%y %s %P\n' 2> /dev/null | head -40; } >> "$R/run.txt"
  fi
  kill "$STUB" 2> /dev/null
  local DOC
  DOC=$(python3 -c "import json;print(json.load(open('$R/summary.json'))['docDir'])" 2> /dev/null)
  if [ -n "$DOC" ] && [ -d "$DOC" ]; then cp -r "$DOC" "$R/doc"; elif [ -n "${LIVE:-}" ] && [ -d "$LIVE" ]; then cp -r "$LIVE" "$R/doc"; fi
  find "$R/doc" -name '.fill' -delete 2> /dev/null
  case "${PRE:-}" in tmpfs=*) sudo umount "$R/data/documents" 2> /dev/null ;; esac
  { python3 "$HERE/bw_facts.py" "$R" "$ENGINE"; cat "$R/pre.txt" 2> /dev/null; cat "$R/run.txt"; } | tee "$R/facts.txt"
}

# long_meeting <case> <engine> <seconds>: one meeting of that length, the fixture audio looped on
# both tracks. Samples memory and CPU every minute and file sizes every 10 minutes.
long_meeting() {
  local C=$1 ENGINE=$2 LEN=$3
  local R=$WORK/$C
  profile "$R" "$ENGINE" 8899
  python3 "$HERE/bw_stub.py" 8899 "$R/stub.jsonl" "" &
  local STUB=$!
  sleep 1
  local P t0 peak=0 rss
  P=$(launch "$R" "$R/app.log" "" RUST_LOG=warn,cap_gpui=info ${DOCS_ENV-CAP_GPUI_DOCS_OPEN=live} \
    CAP_GPUI_AUTO_MEETING=$LEN CAP_GPUI_AUTO_MEETING_LINGER=240 CAP_GPUI_AUTO_MEETING_OUT="$R/summary.json")
  for _ in $(seq 900); do grep -qa "CAP_GPUI_AUTO_MEETING: recording" "$R/app.log" && break; sleep 0.1; done
  t0=$(epoch)
  ( while :; do pacat --playback --device=micsink --raw --format=float32le --rate=48000 --channels=1 < "$MEET/mic.f32"; done ) &
  local MICL=$!
  ( while :; do paplay --device=room "$MEET/sys.wav"; done ) &
  local SYSL=$!
  local LIVE= i=0 drained=
  while kill -0 "$P" 2> /dev/null && [ ! -s "$R/summary.json" ]; do
    sleep 60; i=$((i + 1))
    [ -z "$LIVE" ] && LIVE=$(ls -d "$R/data/documents"/*/ 2> /dev/null | head -1)
    rss=$(tree_rss_kb "$P"); [ "$rss" -gt "$peak" ] && peak=$rss
    echo "t=${i}m rss_mb=$((rss / 1024)) cpu_s=$(cpu_s "$P") fds=$(ls /proc/$P/fd 2> /dev/null | wc -l) threads=$(ls /proc/$P/task 2> /dev/null | wc -l)" >> "$R/samples.txt"
    if [ $((i % 10)) -eq 0 ] && [ -n "$LIVE" ]; then
      echo "t=${i}m files $(find "$LIVE" -type f -printf '%s %P;' | tr '\n' ' ')" >> "$R/files.txt"
      shot "$R/shots/t${i}m.png"
    fi
    if [ -z "$drained" ] && grep -qa "CAP_GPUI_AUTO_MEETING: drained" "$R/app.log"; then
      drained=1; echo "drained after $(( ($(epoch) - t0) / 1000 )) s" >> "$R/run.txt"
      kill "$MICL" "$SYSL" 2> /dev/null; pkill -f "pacat --playback"; pkill paplay
    fi
    [ "$i" -gt $(( LEN / 60 + 30 )) ] && break
  done
  kill "$MICL" "$SYSL" 2> /dev/null; pkill -f "pacat --playback"; pkill paplay
  shot "$R/shots/end.png"
  echo "alive_at_end $(kill -0 "$P" 2> /dev/null && echo yes || echo no) peak_rss_mb=$((peak / 1024)) cpu_s=$(cpu_s "$P")" >> "$R/run.txt"
  kill "$P" 2> /dev/null; sleep 2; kill -9 "$P" 2> /dev/null; kill "$STUB" 2> /dev/null
  local DOC
  DOC=$(python3 -c "import json;print(json.load(open('$R/summary.json'))['docDir'])" 2> /dev/null)
  [ -z "$DOC" ] && DOC=$LIVE
  [ -n "$DOC" ] && [ -d "$DOC" ] && cp -r "$DOC" "$R/doc"
  {
    echo "== files at end"; find "$R/doc" -type f -printf '%s %P\n' 2> /dev/null
    audio_facts "$R/doc" "end"
    echo "notes requests: $(grep '/meetings/notes' "$R/stub.jsonl" | cut -c1-300)"
    echo "transcribe requests: $(grep -c '/meetings/transcribe' "$R/stub.jsonl" 2> /dev/null)"
    python3 -c "import json,sys;s=json.load(open(sys.argv[1]));print('summary', {k:s.get(k) for k in ['durationMs','drainMs','cpuSeconds','peakRssBytes','utteranceCount','elapsedMs']})" "$R/summary.json" 2> /dev/null
    echo "panics $(grep -c panicked "$R/app.log")"
    sed 's/\x1b\[[0-9;]*m//g' "$R/app.log" | grep -E " (WARN|ERROR) " | sed -E 's/^[^ ]+ +//' | cut -c1-200 | sort | uniq -c | sort -rn | head -15
  } >> "$R/run.txt"
  { python3 "$HERE/bw_facts.py" "$R" "$ENGINE"; cat "$R/run.txt"; tail -n 5 "$R/samples.txt"; } | tee "$R/facts.txt"
}

# update_mid <case>: an update finishes downloading 40 s into a meeting (harness-only patch: the
# download and signature check are simulated, everything after is the app's own code). A zenity
# stand-in answers every question with its accept button and logs when it was asked.
update_mid() {
  local C=$1 R=$WORK/$1
  local Z=$WORK/zshim-$C; mkdir -p "$Z"
  printf '#!/bin/sh\necho "$(date +%%s%%3N) zenity $*" >> %s/zenity.log\ncase "$*" in *--question*) exit %s ;; esac\nexit 0\n' "$WORK/$C" "${BW_ANSWER:-0}" > "$Z/zenity"
  chmod +x "$Z/zenity"
  mkdir -p "$R"; touch "$R/zenity.log"
  BW_GENERAL_EXTRA='"updateDownloadPolicy":"auto",' EXTRA_ENV="PATH=$Z:$PATH CAP_GPUI_UPDATE_SIMULATION_VERSION=99.0.0 BW_FAKE_APPLY=1 BW_UPDATE_DELAY_SECS=40 ${BW_MORE_ENV:-}" \
    LINGER=330 meeting "$C" local ""
  sleep 2
  local others
  others=$(pgrep -f "^$BIN" | tr '\n' ' ')
  pkill -f "^$BIN"; sleep 1; pkill -9 -f "^$BIN"
  local MIC
  MIC=$(awk '/^mic /{print $2}' "$R/run.txt")
  {
    echo "== update while recording"
    echo "copies still running at the end (relaunched): ${others:-none}"
    echo "questions asked (s after the meeting's audio began):"
    awk -v m="$MIC" '{printf "  +%.0f s %s\n", ($1-m)/1000, substr($0, index($0,$2), 160)}' "$R/zenity.log"
    sed 's/\x1b\[[0-9;]*m//g' "$R/app.log" | grep -E "update|BW:|Cap starting|quit|relaunch|meeting recording|CAP_GPUI_AUTO_MEETING" | sed -E 's/^([^ ]+) +/\1 /' | cut -c1-220 | head -30
  } | tee -a "$R/facts.txt"
}

# update_studio <case>: a 100 s Studio recording (no meeting) while the update arrives 30 s in.
update_studio() {
  local C=$1 R=$WORK/$1
  profile "$R" local 8899
  local Z=$WORK/zshim-$C; mkdir -p "$Z"
  printf '#!/bin/sh\necho "$(date +%%s%%3N) zenity $*" >> %s/zenity.log\nexit 0\n' "$R" > "$Z/zenity"; chmod +x "$Z/zenity"
  touch "$R/zenity.log"
  sed -i 's/"recordingCountdown":0,/"recordingCountdown":0,"updateDownloadPolicy":"auto",/' "$R/data/store"
  local P t0
  t0=$(epoch)
  P=$(launch "$R" "$R/app.log" "" PATH="$Z:$PATH" RUST_LOG=warn,cap_gpui=info,cap_recording=info CAP_GPUI_AUTO_RECORD=studio:100 \
    CAP_GPUI_UPDATE_SIMULATION_VERSION=99.0.0 BW_FAKE_APPLY=1 BW_UPDATE_DELAY_SECS=30)
  for _ in $(seq 100); do grep -qa "auto-record start requested" "$R/app.log" && break; sleep 0.2; done
  sleep 3; shot "$R/shots/clean-capture.png"
  xdotool key --clearmodifiers ctrl+shift+F9; echo "pressed the start shortcut $(( ($(epoch) - t0) / 1000 )) s after launch" >> "$R/run.txt"
  sleep 15; shot "$R/shots/recording.png"
  sleep 182
  shot "$R/shots/end.png"
  local others; others=$(pgrep -f "^$BIN" | tr '\n' ' ')
  pkill -f "^$BIN"; sleep 1; pkill -9 -f "^$BIN"
  {
    echo "== update during a Studio recording (base and fix should match)"
    cat "$R/run.txt" 2> /dev/null
    echo "copies at the end: ${others:-none}"
    echo "questions asked (s after launch):"
    awk -v m="$t0" '{printf "  +%.0f s %s\n", ($1-m)/1000, substr($0, index($0,$2), 160)}' "$R/zenity.log"
    sed 's/\x1b\[[0-9;]*m//g' "$R/app.log" | grep -iE "update|BW:|Cap starting|quit|relaunch|recording|auto.record|clean capture|phase" | grep -v "cap_recording::sources" | sed -E 's/^([^ ]+) +/\1 /' | cut -c1-200 | head -40
    echo "recordings: $(find "$R/home" "$R/data" -name '*.cap' -maxdepth 6 -type d 2> /dev/null | wc -l)"
  } | tee "$R/facts.txt"
}

tls_setup() { # a private CA the OS trusts (like a company's TLS inspection) and a server cert from it
  local T=$WORK/tls; mkdir -p "$T"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$T/ca.key" -out "$T/ca.pem" -days 2 -subj "/CN=BW Inspection CA" 2> /dev/null
  openssl req -newkey rsa:2048 -nodes -keyout "$T/server.key" -out "$T/server.csr" -subj "/CN=cap-proxy.test" 2> /dev/null
  printf 'subjectAltName=DNS:cap-proxy.test\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' > "$T/ext.cnf"
  openssl x509 -req -in "$T/server.csr" -CA "$T/ca.pem" -CAkey "$T/ca.key" -CAcreateserial -out "$T/server.pem" -days 2 -extfile "$T/ext.cnf" 2> /dev/null
  sudo cp "$T/ca.pem" /usr/local/share/ca-certificates/bw-inspection.crt && sudo update-ca-certificates > /dev/null 2>&1
  grep -q cap-proxy.test /etc/hosts || echo "127.0.0.1 cap-proxy.test" | sudo tee -a /etc/hosts > /dev/null
}

case "$GROUP" in
  cal|cal-base)
    calendar cal-amp-desc amp-desc 4G
    calendar cal-amp-att amp-att 4G
    calendar cal-huge huge 4G
    calendar cal-weird weird 4G
    ;;
  links|links-base)
    hostile_prompt prompt-hostile hostile
    hostile_prompt prompt-lookalike lookalike
    ;;
  disk|disk-base)
    audio_setup
    PRE=tmpfs=24m MIDWAY=fill RELAUNCH=1 meeting disk-fill-midway cloud ""
    PRE=tmpfs=24m MIDWAY=fillstop RELAUNCH=1 meeting disk-fill-at-stop cloud ""
    PRE=docfile meeting docs-is-file local ""
    PRE=docloop meeting docs-symlink-loop local ""
    ;;
  life2|life2-base)
    audio_setup
    MIDWAY=kill9 RELAUNCH=1 meeting kill9-mid local ""
    MIDWAY=kill9 RELAUNCH=1 meeting kill9-mid-cloud cloud ""
    KILL_AT_NOTES=1 RELAUNCH=1 meeting kill9-notes cloud "notes:slow35"
    MIDWAY=second meeting second-instance local ""
    meeting notes-hostile cloud "hostile"
    ;;
  hostile|hostile-base)
    audio_setup
    meeting notes-hostile cloud "hostile"
    ;;
  upd|upd-base)
    audio_setup
    update_mid update-mid-meeting
    ;;
  updfail|updfail-base)
    audio_setup
    BW_MORE_ENV="BW_FAIL_DOWNLOAD=1" update_mid update-fails-mid-meeting
    ;;
  upd2|upd2-base)
    audio_setup
    update_studio update-studio
    ;;
  cpu|cpu-base)
    audio_setup
    LONG_SECS=600 long_meeting cpu-cloud-window cloud 600
    DOCS_ENV="" long_meeting cpu-cloud-nowindow cloud 600
    DOCS_ENV="" long_meeting cpu-local-nowindow local 600
    ;;
  long-local|long-local-base)
    audio_setup
    long_meeting long-local local "${LONG_SECS:-10800}"
    ;;
  long-cloud|long-cloud-base)
    audio_setup
    long_meeting long-cloud cloud "${LONG_SECS:-10800}"
    ;;
  life|life-base)
    audio_setup
    MIDWAY=clock meeting clock-jump local ""
    MIDWAY=second meeting second-instance local ""
    tls_setup
    TLS=1 meeting tls-inspection cloud ""
    ;;
esac
ls "$WORK"
