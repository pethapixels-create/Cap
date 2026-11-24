#!/usr/bin/env bash
# b2b.sh <app> <out> <variant>: a sales rep's back-to-back calendar, through the real app on Linux.
# Meeting A (now+30 s .. now+100 s) runs over; meeting B starts at now+100 s. Calendar prompts on
# (60 s lead), the default. The rep presses the prompt's main button for A, then for B while A is
# still recording, the way they would (a real click at the button AT-SPI reports).
#   variant manual  autoRecord off (default)     variant auto  autoRecord scheduled, 1 s countdown
# Mic audio from a PulseAudio null sink, cloud engine against stub_server.py. Scratch data only.
set -u
APP=$1; OUT=$2; VAR=${3:-manual}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
CI=$ROOT/scripts/ci/meetings
FIX=$ROOT/apps/desktop-gpui/tests/fixtures/meetings
R="$OUT/b2b-$VAR"; D="$R/data"; F="$R/facts.txt"
mkdir -p "$D/meetings" "$D/home" "$D/feed" "$R/shots"
log() { echo "$(date +%T) $*" | tee -a "$F"; }
grab() { import -window root "$R/shots/$1.png" 2>/dev/null; }

pulse_dir=$(mktemp -d)
pulseaudio -n --daemonize=yes --exit-idle-time=-1 \
  -L "module-native-protocol-unix socket=$pulse_dir/native auth-anonymous=1" \
  -L "module-null-sink sink_name=room sink_properties=device.description=Room" \
  -L "module-null-sink sink_name=micsink sink_properties=device.description=MicFeed" \
  -L "module-remap-source master=micsink.monitor source_name=mic source_properties=device.description=Mic"
export PULSE_SERVER=unix:$pulse_dir/native
for _ in $(seq 40); do pactl info > /dev/null 2>&1 && break; sleep 0.25; done
pactl set-default-sink room; pactl set-default-source mic
ffmpeg -v error -y -i "$FIX/you.opus" -f f32le -ac 1 -ar 48000 "$R/mic.f32"

python3 - "$D/feed/feed.ics" << 'PY'
import datetime, sys
now = datetime.datetime.now(datetime.timezone.utc)
f = lambda s: (now + datetime.timedelta(seconds=s)).strftime("%Y%m%dT%H%M%SZ")
ev = lambda uid, a, b, t, who: (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{f(a)}\r\nDTEND:{f(b)}\r\nSUMMARY:{t}\r\n"
    f"ATTENDEE;CN={who}:mailto:x@example.com\r\nEND:VEVENT\r\n")
open(sys.argv[1], "w", newline="").write("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Work\r\n"
    + ev("cd-a", 30, 100, "Discovery call with Northwind", "Ana Ruiz")
    + ev("cd-b", 100, 1900, "Demo with Contoso", "Kofi Mensah") + "END:VCALENDAR\r\n")
PY
(cd "$D/feed" && exec python3 -m http.server 8787 --bind 127.0.0.1 > /dev/null 2>&1) &
FEED=$!
python3 "$CI/stub_server.py" 8899 "$R/stub.jsonl" &
STUB=$!
AUTO=off; [ "$VAR" = auto ] && AUTO=scheduled
printf '%s' "{\"enabled\": true, \"setupCompleted\": true, \"notifications\": {\"scheduled\": true, \"detected\": false, \"leadSecs\": 60}, \"automation\": {\"autoRecord\": \"$AUTO\", \"countdownSecs\": 1}, \"transcription\": {\"engine\": \"cloud\", \"live\": true}}" > "$D/meetings/settings.json"
printf '%s' '{"sources":[{"id":"ics-cd","name":"Work","config":{"kind":"ics","url":"http://127.0.0.1:8787/feed.ics"},"status":{}}]}' > "$D/meetings/calendars.json"
echo "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"recordingCountdown\":0,\"serverUrl\":\"http://127.0.0.1:8899\"},\"auth\":{\"secret\":{\"api_key\":\"ci-test-key\"},\"user_id\":\"citestuser0001\",\"plan\":{\"upgraded\":true,\"manual\":false,\"last_checked\":$(date +%s)},\"organizations\":[]}}" > "$D/store"
sleep 1
T0=$(date +%s)
env HOME="$D/home" CAP_GPUI_APP_DATA_DIR="$D" CAP_GPUI_NO_TRAY=1 RUST_LOG=warn,cap_gpui=info,cap_meetings=info \
  "$APP" < /dev/null > "$R/app.log" 2>&1 &
P=$!
( while kill -0 $P 2>/dev/null; do pacat --playback --device=micsink --raw --format=float32le --rate=48000 --channels=1 < "$R/mic.f32"; done ) &
MIC=$!
at() { local s=$1; while [ $(( $(date +%s) - T0 )) -lt "$s" ]; do sleep 0.5; done; }
press() { # <label regex> <tag>: click the first showing button with that accessible name
  local xy="" i
  for i in $(seq 1 6); do
    xy=$(python3 "$HERE/atspi.py" where "$P" "$1" 2>/dev/null | head -1)
    [ -n "$xy" ] && break; sleep 0.5
  done
  if [ -z "$xy" ]; then
    # Nothing exposed through AT-SPI: the top prompt's main button sits at a fixed spot on this
    # 1600x1000 screen (prompts stack from the top right).
    xy="1489 62"; log "$2: no accessible button matching /$1/, clicking the top prompt at $xy"
  fi
  xdotool mousemove ${xy% *} ${xy#* } click 1
  log "$2: pressed /$1/ at $xy"
}
docs() { # what the library holds: one line per meeting doc
  python3 - "$D/documents" << 'PY'
import json, os, sys
root = sys.argv[1]
for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
    p = os.path.join(root, d, "doc.json")
    if not os.path.isfile(p):
        continue
    j = json.load(open(p))
    m = j.get("meeting") or {}
    ev = (m.get("event") or {}).get("title")
    tx = os.path.join(root, d, "transcript.jsonl")
    n = sum(1 for _ in open(tx)) if os.path.isfile(tx) else 0
    print(f"doc {d[:8]} title={j.get('title')!r} event={ev!r} transcript_lines={n} stop={m.get('stopReason')}")
PY
}
recording() { grep -aoE "Recording|Notes ready|Writing notes" "$R/app.log" | tail -1; }

timeout 20 python3 "$HERE/control.py" >> "$F" 2>&1 &
if [ "$VAR" = manual ]; then
  at 6; grab 0-start
  press "Take notes|Join and take notes" "A prompt"   # A shows at once (starts in 30 s, lead 60 s)
  at 20; grab 1-a-recording
  log "after A accept: $(docs | tr '\n' ';')"
  at 46                                                  # B's prompt is due at now+40 s
  grab 2-b-prompt
  press "Take notes|Join and take notes" "B prompt (A still recording)"
  at 60; grab 3-after-b-accept
  log "after B accept: $(docs | tr '\n' ';')"
  at 112; grab 4-b-started
  log "at B start +12 s: $(docs | tr '\n' ';')"
else
  at 40; grab 1-a-auto
  log "A auto-started? $(docs | tr '\n' ';')"
  at 112; grab 2-b-start
  log "at B start +12 s: $(docs | tr '\n' ';')"
  at 130; grab 3-b-start-30
  log "at B start +30 s: $(docs | tr '\n' ';')"
fi
python3 "$HERE/atspi.py" tree "$P" > "$R/atspi-end.txt" 2>&1
kill $P 2>/dev/null; sleep 4; kill -9 $P 2>/dev/null; kill $MIC $FEED $STUB 2>/dev/null; pkill -f "pacat --playback" 2>/dev/null
log "final: $(docs | tr '\n' ';')"
mkdir -p "$R/docs"; for j in "$D"/documents/*/doc.json; do [ -f "$j" ] && cp "$j" "$R/docs/$(basename "$(dirname "$j")").json"; done
sed 's/\x1b\[[0-9;]*m//g' "$R/app.log" | grep -aiE "prompt|recording|meeting (start|stop)|replaced|panicked" | head -40 > "$R/log-excerpt.txt"
pulseaudio --kill 2>/dev/null
cat "$F"
