"""bw_os.py <app binary> <work dir> <case> <feed kind>: the real app (macOS or Windows) subscribed to
one ICS feed on loopback, signed in to a local address that answers nothing. Samples the app's
memory and CPU for 150 s (stops early above 6 GB so the runner survives), then relaunches with the
feed gone for 60 s, reading only the cache. Writes <work>/<case>/facts.txt. Nothing reaches a server.
"""
import http.server, json, os, shutil, subprocess, sys, threading, time
import psutil

BIN, WORK, CASE, KIND = sys.argv[1:5]
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(WORK, CASE)
DATA = os.path.join(R, "data")
FEED = os.path.join(R, "feed")
CEILING = int(os.environ.get("BW_CEILING_GB", "6")) * 1024**3
for d in (os.path.join(DATA, "meetings"), FEED, os.path.join(R, "logs"), os.path.join(R, "home")):
    os.makedirs(d, exist_ok=True)

size = subprocess.run([sys.executable, os.path.join(HERE, "bw_feed.py"), KIND, os.path.join(FEED, "feed.ics")],
                      capture_output=True, text=True).stdout.strip()
json.dump({"enabled": True, "setupCompleted": True,
           "notifications": {"scheduled": True, "detected": False, "leadSecs": 120},
           "transcription": {"engine": "local", "live": True}},
          open(os.path.join(DATA, "meetings", "settings.json"), "w"))
json.dump({"general_settings": {"hasCompletedOnboarding": True, "hasCompletedStartup": True,
                                "enableNotifications": False, "enableTelemetry": False,
                                "serverUrl": "http://127.0.0.1:9"},
           "auth": {"secret": {"api_key": "ci-test-key"}, "user_id": "citestuser0001",
                    "plan": {"upgraded": True, "manual": False, "last_checked": int(time.time())},
                    "organizations": []}},
          open(os.path.join(DATA, "store"), "w"))
json.dump({"sources": [{"id": "ics-bw", "name": "Work",
                        "config": {"kind": "ics", "url": "http://127.0.0.1:8777/feed.ics"}, "status": {}}]},
          open(os.path.join(DATA, "meetings", "calendars.json"), "w"))

requests = []


class Feed(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=FEED, **k)

    def log_message(self, *a):
        requests.append(self.path)


server = http.server.ThreadingHTTPServer(("127.0.0.1", 8777), Feed)
threading.Thread(target=server.serve_forever, daemon=True).start()

env = dict(os.environ, CAP_GPUI_APP_DATA_DIR=DATA, CAP_GPUI_LOG_DIR=os.path.join(R, "logs"),
           CAP_GPUI_NO_TRAY="1", RUST_LOG="warn,cap_gpui=info", CAP_GPUI_LIBRARY_WINDOW="1",
           CAP_GPUI_AUTO_LIBRARY="home")


def tree_rss(p):
    total = 0
    for q in [p] + p.children(recursive=True):
        try:
            total += q.memory_info().rss
        except psutil.Error:
            pass
    return total


def cpu(p):
    try:
        t = p.cpu_times()
        return round(t.user + t.system, 1)
    except psutil.Error:
        return -1


def run(label, seconds, out):
    log = open(os.path.join(R, f"app-{label}.log"), "w")
    proc = subprocess.Popen([BIN], env=env, stdout=log, stderr=subprocess.STDOUT)
    p = psutil.Process(proc.pid)
    peak, died, stopped, t0 = 0, None, None, time.time()
    for i in range(seconds):
        if proc.poll() is not None:
            died = round(time.time() - t0)
            break
        rss = tree_rss(p)
        peak = max(peak, rss)
        if i % 10 == 9:
            out.append(f"{label} t={i + 1}s rss_mb={rss // 2**20} cpu_s={cpu(p)}")
        if rss > CEILING:
            stopped = round(time.time() - t0)
            break
        time.sleep(1)
    c = cpu(p)
    for q in [p] + (p.children(recursive=True) if proc.poll() is None else []):
        try:
            q.kill()
        except psutil.Error:
            pass
    proc.wait(timeout=30)
    return f"{label}: peak_rss_mb={peak // 2**20} died_at_s={died or 'no'} stopped_above_6GB_at_s={stopped or 'no'} cpu_s={c}"


samples = []
first = run("first", 150, samples)
cache = sorted(((os.path.getsize(os.path.join(dp, f)), os.path.relpath(os.path.join(dp, f), DATA))
                for dp, _, fs in os.walk(os.path.join(DATA, "meetings")) for f in fs), reverse=True)[:3]
server.shutdown()
shutil.rmtree(FEED, ignore_errors=True)
second = run("relaunch", 60, samples)
facts = [f"{KIND}: {size}", first, f"feed requests: {sum('feed.ics' in r for r in requests)}",
         "cache files: " + "; ".join(f"{s} {n}" for s, n in cache), second]
panics = 0
for name in os.listdir(R):
    if name.startswith("app-"):
        panics += open(os.path.join(R, name), errors="replace").read().count("panicked")
facts.append(f"panics {panics}")
open(os.path.join(R, "facts.txt"), "w").write("\n".join(facts + samples) + "\n")
print("\n".join(facts))
