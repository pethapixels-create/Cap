#!/bin/bash
# mac.sh <cap-gpui> <work> <fixture dir>: one scripted meeting through the real app on macOS (scratch,
# BT A/B only). Mic side through the app's scripted microphone, the call through afplay to the
# default output (the app's system audio tap). Pro account against stub_server.py.
set -uo pipefail
BIN=$1; R=$2; FIX=$3
HERE=$(cd "$(dirname "$0")" && pwd)
MODELS=${CAP_MEETINGS_MODELS:?}
mkdir -p "$R/home/Library/Application Support/so.cap.desktop/transcription_models" "$R/data/meetings" "$R/logs"
for f in "$MODELS"/*; do ln -sfn "$f" "$R/home/Library/Application Support/so.cap.desktop/transcription_models/"; done
ffmpeg -v error -y -i "$FIX/you.opus" -ac 1 -ar 48000 -f f32le "$R/mic.f32"
ffmpeg -v error -y -i "$FIX/others.opus" -ac 2 -ar 48000 "$R/others.wav"
DUR=$(python3 -c "import json;print(json.load(open('$FIX/truth.json'))['duration_ms']//1000)")
echo '{"enabled": true, "setupCompleted": true, "transcription": {"engine": "local", "live": true}}' > "$R/data/meetings/settings.json"
echo "{\"general_settings\":{\"hasCompletedOnboarding\":true,\"hasCompletedStartup\":true,\"enableNotifications\":false,\"enableTelemetry\":false,\"recordingCountdown\":0,\"serverUrl\":\"http://127.0.0.1:8899\"},\"auth\":{\"secret\":{\"api_key\":\"ci-test-key\"},\"user_id\":\"citestuser0001\",\"plan\":{\"upgraded\":true,\"manual\":false,\"last_checked\":$(date +%s)},\"organizations\":[]}}" > "$R/data/store"
echo "2000 $R/mic.f32" > "$R/mic_script.txt"
python3 "$HERE/stub_server.py" 8899 "$R/stub.jsonl" & STUB=$!
sleep 1
env HOME="$R/home" CAP_GPUI_APP_DATA_DIR="$R/data" CAP_GPUI_LOG_DIR="$R/logs" CAP_GPUI_NO_TRAY=1 \
  RUST_LOG=warn,cap_gpui=info,cap_meetings=info \
  CAP_GPUI_AUTO_MEETING=$((DUR + 12)) CAP_GPUI_AUTO_MEETING_LINGER=70 CAP_GPUI_AUTO_MEETING_OUT="$R/summary.json" \
  CAP_GPUI_AUTO_MEETING_MIC_SCRIPT="$R/mic_script.txt" CAP_GPUI_AUTO_MEETING_MARKS="$R/marks.txt" \
  CAP_GPUI_AUTO_MEETING_STOP_FILE="$R/page_started.log" \
  "$BIN" < /dev/null > "$R/app.log" 2>&1 &
P=$!
for _ in $(seq 900); do grep -qa "CAP_GPUI_AUTO_MEETING: recording" "$R/app.log" "$R"/logs/* 2>/dev/null && break; kill -0 $P 2>/dev/null || break; sleep 0.1; done
sleep 2
echo "started $(python3 -c 'import time;print(int(time.time()*1000))')" > "$R/page_started.log"
afplay "$R/others.wav" & PLAY=$!
for _ in $(seq 3000); do [ -s "$R/summary.json" ] && break; kill -0 $P 2>/dev/null || break; sleep 0.2; done
kill $PLAY $STUB 2>/dev/null; kill $P 2>/dev/null; sleep 1; kill -9 $P 2>/dev/null
cat "$R"/logs/* >> "$R/app.log" 2>/dev/null
t0=$(grep '^t0 ' "$R/marks.txt" | head -1 | cut -d' ' -f2); [ -n "$t0" ] && echo "mic $((t0 + 3000))" > "$R/run.txt"
DOC=$(python3 -c "import json;print(json.load(open('$R/summary.json'))['docDir'])" 2>/dev/null)
cp "$DOC/transcript.jsonl" "$R/" 2>/dev/null
python3 "$HERE/offsets.py" "$R" "$DOC" > "$R/offsets.json"
python3 "$HERE/score.py" "$DOC/transcript.jsonl" "$FIX/truth.json" "$R/offsets.json" > "$R/score.json" 2> "$R/score.err"
cat "$R/score.json"
