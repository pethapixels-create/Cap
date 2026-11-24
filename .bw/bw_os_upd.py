"""bw_os_upd.py <app binary> <work dir> <case> <ax tool or "">: W11 on macOS/Windows. The real app (built
with the harness-only update simulation patch) records a Notes meeting (cloud engine, local stub);
an update "finishes downloading" 40 s after launch. Every second the run looks for the "Update Cap"
question and presses its Restart Now button the way a person would (Windows UI Automation, macOS
Accessibility via axtool). Writes <work>/<case>/facts.txt. Nothing reaches a Cap server.
"""
import json, os, subprocess, sys, time
import psutil

BIN, WORK, CASE, AX = sys.argv[1:5]
AX = "" if AX == "none" else AX
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(WORK, CASE)
DATA = os.path.join(R, "data")
LOGS = os.path.join(R, "logs")
for d in (os.path.join(DATA, "meetings"), LOGS):
    os.makedirs(d, exist_ok=True)
WIN = sys.platform == "win32"

json.dump({"enabled": True, "setupCompleted": True,
           "notifications": {"scheduled": False, "detected": False, "leadSecs": 120},
           "transcription": {"engine": "cloud", "live": True}},
          open(os.path.join(DATA, "meetings", "settings.json"), "w"))
json.dump({"general_settings": {"hasCompletedOnboarding": True, "hasCompletedStartup": True,
                                "enableNotifications": False, "enableTelemetry": False,
                                "recordingCountdown": 0, "updateDownloadPolicy": "auto",
                                "serverUrl": "http://127.0.0.1:8899"},
           "auth": {"secret": {"api_key": "ci-test-key"}, "user_id": "citestuser0001",
                    "plan": {"upgraded": True, "manual": False, "last_checked": int(time.time())},
                    "organizations": []}},
          open(os.path.join(DATA, "store"), "w"))

stub = subprocess.Popen([sys.executable, os.path.join(HERE, "bw_stub.py"), "8899", os.path.join(R, "stub.jsonl"), ""])
time.sleep(1)
env = dict(os.environ, CAP_GPUI_APP_DATA_DIR=DATA, CAP_GPUI_LOG_DIR=LOGS, CAP_GPUI_NO_TRAY="1",
           RUST_LOG="warn,cap_gpui=info,cap_meetings=info", CAP_GPUI_DOCS_OPEN="live",
           CAP_GPUI_AUTO_MEETING="150", CAP_GPUI_AUTO_MEETING_LINGER="330",
           CAP_GPUI_AUTO_MEETING_OUT=os.path.join(R, "summary.json"),
           CAP_GPUI_UPDATE_SIMULATION_VERSION="99.0.0", BW_FAKE_APPLY="1", BW_UPDATE_DELAY_SECS="40")
out = open(os.path.join(R, "app.log"), "w")
t0 = time.time()
app = subprocess.Popen([BIN], env=env, stdout=out, stderr=subprocess.STDOUT)
NAME = os.path.basename(BIN)

UIA = r'''
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "Update Cap")
$w = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $cond)
if (-not $w) { exit 3 }
$b = $w.FindFirst([System.Windows.Automation.TreeScope]::Descendants, (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, "Restart Now")))
if (-not $b) { exit 4 }
$b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
exit 0
'''


def press_restart():
    if WIN:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", UIA], capture_output=True, text=True, timeout=30)
        return r.returncode == 0, r.returncode
    if not AX:
        return False, "no axtool"
    found = subprocess.run([AX, "find", NAME, "^Restart Now$"], capture_output=True, text=True, timeout=30)
    if found.returncode != 0 or not found.stdout.strip():
        return False, found.returncode
    shot(f"prompt-{int(time.time() - t0)}s")
    r = subprocess.run([AX, "press", NAME, "^Restart Now$"], capture_output=True, text=True, timeout=30)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:200]


def shot(label):
    path = os.path.join(R, f"{label}.png")
    if WIN:
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
              "$g=[System.Drawing.Graphics]::FromImage($i);$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
              f"$i.Save('{path}')")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=30)
    else:
        subprocess.run(["screencapture", "-x", path], capture_output=True, timeout=30)


events = []
pressed = None
for i in range(420):
    time.sleep(1)
    if pressed is None and i >= 30:
        ok, detail = press_restart()
        if ok:
            pressed = round(time.time() - t0)
            events.append(f"pressed Restart Now at +{pressed} s after launch ({detail})")
    if i in (60, 200):
        shot(f"t{i}s")
    if os.path.exists(os.path.join(R, "summary.json")) and pressed is None and i > 380:
        break

for p in psutil.process_iter(["name", "exe"]):
    try:
        if p.info["name"] and p.info["name"].lower().startswith(NAME.lower().split(".")[0]):
            events.append(f"still running at the end: pid {p.pid}")
            p.kill()
    except psutil.Error:
        pass
stub.kill()

lines = []
for name in sorted(os.listdir(LOGS)) + ["../app.log"]:
    path = os.path.join(LOGS, name)
    if os.path.isfile(path):
        for line in open(path, errors="replace"):
            if any(k in line for k in ("Cap starting", "updates", "BW:", "quit requested", "meeting recording", "CAP_GPUI_AUTO_MEETING")):
                lines.append(line.strip()[:220])
summary = {}
try:
    summary = json.load(open(os.path.join(R, "summary.json")))
except Exception:
    pass
docs = os.path.join(DATA, "documents")
metas = []
if os.path.isdir(docs):
    for d in sorted(os.listdir(docs)):
        try:
            m = json.load(open(os.path.join(docs, d, "doc.json"))).get("meeting") or {}
            metas.append(f"doc {d}: durationMs={m.get('durationMs')} stopReason={m.get('stopReason')}")
        except Exception:
            pass
facts = [f"platform {sys.platform}", f"pressed: {pressed or 'never (no Update Cap question found)'}"] + events + metas + \
    [f"summary durationMs={summary.get('durationMs')}"] + sorted(set(lines), key=lines.index)[:40]
open(os.path.join(R, "facts.txt"), "w").write("\n".join(facts) + "\n")
print("\n".join(facts))
