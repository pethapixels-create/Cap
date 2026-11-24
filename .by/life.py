#!/usr/bin/env python3
"""life.py: Cap Notes lifecycle measurements on one machine (Linux, macOS, Windows). Scratch.

  life.py launch  --out D --arm name=PKG[:notes] ... [--rounds 7]
  life.py idle    --out D --arm name=PKG[:notes] ... [--rounds 2] [--settle 45] [--secs 180]
  life.py meeting --out D --pkg PKG --fix FIXDIR --models M [--mins 60] [--engine default|local|cloud]
  life.py ui      --out D --pkg PKG [--runs 3]
  life.py summary D...

PKG is an unpacked by-build package (bin/, lib/, Frameworks/). Every run gets a fresh scratch profile
under D; nothing touches a real Cap profile. Linux needs DISPLAY (and PULSE_SERVER for meetings),
Windows needs a desktop session. Results: D/<cmd>.jsonl, one JSON object per run.
"""
import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

OS = platform.system()  # Linux, Darwin, Windows
HERE = Path(__file__).resolve().parent
STUB = HERE / "stub_server.py"
LOG_FILTER = "warn,cap_gpui=info,cap_meetings=info,by_line=info,by_origin=info,by_notes=info,by_stop=info,by_frames=info"


def now_ms():
    return int(time.time() * 1000)


def exe(pkg):
    return Path(pkg).resolve() / "bin" / ("cap-gpui.exe" if OS == "Windows" else "cap-gpui")


def write_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj))


def append(out, name, row):
    with open(Path(out) / f"{name}.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row)[:600], flush=True)


# ---------------------------------------------------------------- profile + spawn
def profile(r, notes, models=None, engine="default", stub_port=None):
    r = Path(r).resolve()
    for d in ("home", "data/meetings", "logs", "rec"):
        (r / d).mkdir(parents=True, exist_ok=True)
    gs = {"hasCompletedOnboarding": True, "hasCompletedStartup": True, "enableNotifications": False,
          "enableTelemetry": False, "recordingCountdown": 0}
    store = {"general_settings": gs}
    if stub_port:
        gs["serverUrl"] = f"http://127.0.0.1:{stub_port}"
        store["auth"] = {"secret": {"api_key": "ci-test-key"}, "user_id": "citestuser0001",
                         "plan": {"upgraded": True, "manual": False, "last_checked": int(time.time())},
                         "organizations": []}
    write_json(r / "data/store", store)
    if notes:
        s = {"enabled": True, "setupCompleted": True}
        if engine != "default":
            s["transcription"] = {"engine": engine, "live": True}
        write_json(r / "data/meetings/settings.json", s)
    if models:
        if OS == "Darwin":
            m = r / "home/Library/Application Support/so.cap.desktop/transcription_models"
        elif OS == "Windows":
            m = r / "home/AppData/Local/so.cap.desktop/transcription_models"
        else:
            m = r / "home/.local/share/so.cap.desktop/transcription_models"
        m.mkdir(parents=True, exist_ok=True)
        for f in Path(models).resolve().iterdir():
            dst = m / f.name
            if OS == "Windows":
                if f.is_dir():
                    subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(f)], capture_output=True)
                else:
                    try:
                        os.link(f, dst)
                    except OSError:
                        shutil.copy2(f, dst)
            else:
                dst.symlink_to(f)
    return r


def app_env(pkg, r, extra=None):
    pkg, r = Path(pkg).resolve(), Path(r).resolve()
    env = dict(os.environ)
    env.update({"CAP_GPUI_APP_DATA_DIR": str(r / "data"), "CAP_GPUI_LOG_DIR": str(r / "logs"),
                "CAP_GPUI_RECORDINGS_DIR": str(r / "rec"), "CAP_GPUI_NO_TRAY": "1", "RUST_LOG": LOG_FILTER})
    home = r / "home"
    if OS == "Windows":
        (home / "AppData/Local").mkdir(parents=True, exist_ok=True)
        (home / "AppData/Roaming").mkdir(parents=True, exist_ok=True)
        (home / "tmp").mkdir(parents=True, exist_ok=True)
        env.update({"USERPROFILE": str(home), "LOCALAPPDATA": str(home / "AppData/Local"),
                    "APPDATA": str(home / "AppData/Roaming"), "TEMP": str(home / "tmp"), "TMP": str(home / "tmp")})
        env["PATH"] = str(pkg / "bin") + ";" + env.get("PATH", "")
        ort = list((pkg / "bin").glob("onnxruntime*.dll"))
    else:
        env["HOME"] = str(home)
        lib = pkg / "lib"
        if OS == "Linux":
            env["LD_LIBRARY_PATH"] = str(lib)
            ort = sorted(lib.glob("libonnxruntime.so*"))
        else:
            ort = sorted(lib.glob("libonnxruntime*.dylib"))
    if ort:
        env["ORT_DYLIB_PATH"] = str(ort[0])
    env.update(extra or {})
    return env


def spawn(pkg, r, extra=None):
    out = open(Path(r) / "app.out", "ab")
    kw = {}
    if OS == "Windows":
        kw["creationflags"] = 0x00000200  # new process group
    else:
        kw["start_new_session"] = True
    t0 = time.time()
    p = subprocess.Popen([str(exe(pkg))], env=app_env(pkg, r, extra), stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, cwd=str(r), **kw)
    return p, t0


def kill(p):
    try:
        procs = [psutil.Process(p.pid)] + psutil.Process(p.pid).children(recursive=True)
    except psutil.Error:
        procs = []
    for q in procs:
        try:
            q.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=8)
    for q in alive:
        try:
            q.kill()
        except psutil.Error:
            pass


def applog(r):
    """The app's log: its stdout (println benches) plus whichever of stdout or the log files holds
    more of the tracing lines, so no line is counted twice."""
    r = Path(r)
    strip = lambda t: re.sub(r"\x1b\[[0-9;]*m", "", t)
    try:
        out = strip((r / "app.out").read_text(errors="replace"))
    except OSError:
        out = ""
    files = ""
    for f in sorted((r / "logs").glob("*")):
        try:
            files += strip(f.read_text(errors="replace"))
        except OSError:
            pass
    key = lambda t: t.count("by line") + t.count("by frames") + t.count("CAP_GPUI_AUTO_MEETING")
    if key(files) > key(out):
        return files + "\n--- stdout ---\n" + _bench_only(out)
    return out


def _bench_only(text):
    return "\n".join(l for l in text.splitlines() if l.startswith("documents-bench") or l.startswith("DOCS READY"))


# ---------------------------------------------------------------- first visible window
class Windows:
    def __init__(self):
        if OS == "Linux":
            self.x = ctypes.cdll.LoadLibrary("libX11.so.6")
            self.x.XOpenDisplay.restype = ctypes.c_void_p
            self.x.XDefaultRootWindow.restype = ctypes.c_ulong
            self.x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            self.x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                          ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                          ctypes.POINTER(ctypes.c_uint)]
            self.x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
            self.x.XFree.argtypes = [ctypes.c_void_p]
            self.d = self.x.XOpenDisplay(None)
            self.root = self.x.XDefaultRootWindow(self.d)
        elif OS == "Darwin":
            self.waiter = HERE / "winwait"
            if not self.waiter.exists():
                subprocess.run(["swiftc", "-O", str(HERE / "winwait.swift"), "-o", str(self.waiter)], check=True)

    def _x_viewable(self):
        r, p, n = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_uint()
        kids = ctypes.POINTER(ctypes.c_ulong)()
        self.x.XQueryTree(self.d, self.root, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n))
        attrs = (ctypes.c_int * 64)()
        out = set()
        for i in range(n.value):
            w = kids[i]
            if self.x.XGetWindowAttributes(self.d, w, attrs):
                # XWindowAttributes on LP64: map_state is at byte offset 92 (int index 23)
                width, height, map_state = attrs[2], attrs[3], attrs[23]
                if map_state == 2 and width > 50 and height > 30:
                    out.add(w)
        if kids:
            self.x.XFree(kids)
        return out

    def before(self):
        if OS == "Linux":
            self.base = self._x_viewable()

    def wait(self, pid, timeout=60):
        """Wall time (s, epoch) when the process first shows a visible window, else None."""
        end = time.time() + timeout
        if OS == "Darwin":
            try:
                out = subprocess.run([str(self.waiter), str(pid), str(timeout)], capture_output=True, text=True,
                                     timeout=timeout + 5).stdout.strip()
                return float(out) if out and float(out) > 0 else None
            except (subprocess.TimeoutExpired, ValueError):
                return None
        while time.time() < end:
            if OS == "Linux":
                if self._x_viewable() - self.base:
                    return time.time()
            else:
                if win_visible(pid):
                    return time.time()
            time.sleep(0.004)
        return None


def win_visible(pid):
    user32 = ctypes.windll.user32
    found = []
    pids = {pid}
    try:
        pids |= {c.pid for c in psutil.Process(pid).children(recursive=True)}
    except psutil.Error:
        pass

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            rect = (ctypes.c_long * 4)()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect[2] - rect[0] > 50 and rect[3] - rect[1] > 30:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return bool(found)


# ---------------------------------------------------------------- sampling
class MacUsage:
    """proc_pid_rusage(RUSAGE_INFO_V2): wakeups, footprint, disk writes."""

    class Info(ctypes.Structure):
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [(n, ctypes.c_uint64) for n in (
            "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups", "pageins", "wired_size",
            "resident_size", "phys_footprint", "proc_start_abstime", "proc_exit_abstime", "child_user_time",
            "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups", "child_pageins",
            "child_elapsed_abstime", "diskio_bytesread", "diskio_byteswritten")]

    def __init__(self):
        self.lib = ctypes.CDLL("/usr/lib/libproc.dylib")

    def get(self, pid):
        info = self.Info()
        if self.lib.proc_pid_rusage(pid, 2, ctypes.byref(info)) != 0:
            return None
        return {"wakeups": info.pkg_idle_wkups + info.interrupt_wkups, "footprint": info.phys_footprint,
                "disk_w": info.diskio_byteswritten}


MAC = MacUsage() if OS == "Darwin" else None


def sample(pid):
    try:
        root = psutil.Process(pid)
        procs = [root] + root.children(recursive=True)
    except psutil.Error:
        return None
    s = {"t": time.time(), "cpu": 0.0, "rss": 0, "ctx": 0, "disk_w": 0, "n": len(procs), "footprint": 0}
    for q in procs:
        try:
            with q.oneshot():
                c = q.cpu_times()
                s["cpu"] += c.user + c.system
                s["rss"] += q.memory_info().rss
                cs = q.num_ctx_switches()
                s["ctx"] += cs.voluntary + cs.involuntary
                if MAC:
                    u = MAC.get(q.pid)
                    if u:
                        s["disk_w"] += u["disk_w"]
                        s["footprint"] += u["footprint"]
                        s["ctx"] = s["ctx"]  # psutil ctx on macOS
                        s.setdefault("wakeups", 0)
                        s["wakeups"] += u["wakeups"]
                else:
                    io = q.io_counters()
                    s["disk_w"] += io.write_bytes
                    if OS == "Windows":
                        s["footprint"] += q.memory_info().private
        except psutil.Error:
            pass
    return s


class Sampler(threading.Thread):
    def __init__(self, pid, every):
        super().__init__(daemon=True)
        self.pid, self.every, self.rows, self.stop = pid, every, [], threading.Event()

    def run(self):
        while not self.stop.is_set():
            s = sample(self.pid)
            if s:
                self.rows.append(s)
            self.stop.wait(self.every)


def rate(rows, key, a=0, b=None):
    rows = rows[a:b]
    if len(rows) < 2 or key not in rows[0]:
        return None
    dt = rows[-1]["t"] - rows[0]["t"]
    return (rows[-1][key] - rows[0][key]) / dt if dt > 0 else None


class Energy:
    """macOS powermetrics (sudo) energy impact + wakeups for the app's processes while it runs."""

    def __init__(self, path, secs, interval=5):
        self.path, self.p = path, None
        if OS != "Darwin" or subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
            return
        n = max(1, int(secs / interval))
        self.p = subprocess.Popen(["sudo", "-n", "powermetrics", "--samplers", "tasks", "--show-process-energy",
                                   "-i", str(interval * 1000), "-n", str(n)], stdout=open(path, "w"),
                                  stderr=subprocess.DEVNULL)

    def finish(self, name="cap-gpui"):
        if not self.p:
            return None
        try:
            self.p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(["sudo", "-n", "kill", str(self.p.pid)])
        impact, intr, idle = [], [], []
        for line in open(self.path, errors="replace"):
            if line.startswith(name):
                nums = re.findall(r"-?\d+\.?\d*", line[len(name):])
                # Name ID CPU ms/s User% Deadlines(<2, 2-5) Wakeups(Intr, Pkg idle) [GPU ms/s] Energy Impact
                if len(nums) >= 8:
                    impact.append(float(nums[-1]))
                    intr.append(float(nums[5]))
                    idle.append(float(nums[6]))
        if not impact:
            return None
        return {"energy_impact_mean": statistics.mean(impact), "energy_impact_p90": sorted(impact)[int(0.9 * (len(impact) - 1))],
                "intr_wakeups_s": statistics.mean(intr), "pkg_idle_wakeups_s": statistics.mean(idle), "samples": len(impact)}


# ---------------------------------------------------------------- commands
def parse_arms(specs):
    arms = []
    for spec in specs:
        name, rest = spec.split("=", 1)
        notes = rest.endswith(":notes")
        arms.append((name, rest[:-6] if notes else rest, notes))
    return arms


def cmd_launch(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    arms = parse_arms(a.arm)
    win = Windows()
    for rnd in range(a.rounds + 1):
        order = arms if rnd % 2 == 0 else list(reversed(arms))
        for name, pkg, notes in order:
            r = profile(out / "runs" / f"launch-{name}-{rnd}", notes, a.models if notes else None)
            win.before()
            p, t0 = spawn(pkg, r)
            shown = win.wait(p.pid, 60)
            time.sleep(max(0, t0 + a.hold - time.time()))
            s = sample(p.pid)
            kill(p)
            row = {"cmd": "launch", "arm": name, "round": rnd, "warmup": rnd == 0,
                   "window_ms": round((shown - t0) * 1000) if shown else None,
                   "rss_mb": s and s["rss"] / 1048576, "footprint_mb": s and s["footprint"] / 1048576,
                   "cpu_s": s and s["cpu"], "procs": s and s["n"]}
            append(out, "launch", row)
            time.sleep(2)


def cmd_idle(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    arms = parse_arms(a.arm)
    for rnd in range(a.rounds):
        order = arms if rnd % 2 == 0 else list(reversed(arms))
        for name, pkg, notes in order:
            r = profile(out / "runs" / f"idle-{name}-{rnd}", notes, a.models if notes else None)
            p, t0 = spawn(pkg, r)
            time.sleep(a.settle)
            energy = Energy(out / "runs" / f"idle-{name}-{rnd}" / "powermetrics.txt", a.secs)
            smp = Sampler(p.pid, 1.0); smp.start()
            time.sleep(a.secs)
            smp.stop.set(); smp.join()
            e = energy.finish()
            rows = smp.rows
            kill(p)
            row = {"cmd": "idle", "arm": name, "round": rnd, "secs": a.secs,
                   "cpu_pct": (rate(rows, "cpu") or 0) * 100, "ctx_s": rate(rows, "ctx"),
                   "wakeups_s": rate(rows, "wakeups"), "disk_w_kb_s": (rate(rows, "disk_w") or 0) / 1024,
                   "rss_mb_end": rows[-1]["rss"] / 1048576 if rows else None,
                   "rss_mb_max": max(x["rss"] for x in rows) / 1048576 if rows else None,
                   "footprint_mb_end": rows[-1]["footprint"] / 1048576 if rows else None,
                   "procs": rows[-1]["n"] if rows else None, "energy": e}
            append(out, "idle", row)
            time.sleep(3)


def stub(port, log):
    p = subprocess.Popen([sys.executable, str(STUB), str(port), str(log)])
    for _ in range(100):
        try:
            import socket
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    return p


def build_fixture(fix, work, loops):
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    mic, sysw = work / "mic.f32", work / f"sys-{loops}.wav"
    if not mic.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(Path(fix) / "you.opus"), "-f", "f32le", "-ac", "1",
                        "-ar", "48000", str(mic)], check=True)
    if not sysw.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-stream_loop", str(loops - 1), "-i", str(Path(fix) / "others.opus"),
                        "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(sysw)], check=True)
    dur = json.load(open(Path(fix) / "truth.json"))["duration_ms"]
    return mic, sysw, dur


def player(path):
    if OS == "Darwin":
        return subprocess.Popen(["afplay", str(path)])
    if OS == "Windows":
        return subprocess.Popen(["ffplay", "-v", "error", "-nodisp", "-autoexit", str(path)])
    return subprocess.Popen(["paplay", str(path)])


def caption_stats(log):
    origin = None
    m = re.search(r"by origin.*?wall=(\d+)", log)
    if m:
        origin = int(m.group(1))
    lags = {"partial": [], "final": []}
    lines = []
    for m in re.finditer(r"by line.*?fin=(\w+).*?src=(\w+).*?end=(\d+).*?wall=(\d+)", log):
        fin, src, end, wall = m.group(1) == "true", m.group(2), int(m.group(3)), int(m.group(4))
        lines.append((wall, fin, src, end))
        if origin:
            lags["final" if fin else "partial"].append(wall - (origin + end))
    def pct(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else None
    out = {}
    for k, xs in lags.items():
        out[k] = {"n": len(xs), "p50_ms": pct(xs, 0.5), "p90_ms": pct(xs, 0.9), "max_ms": max(xs) if xs else None}
        if origin and xs:
            # Lag in the last 10 minutes vs the first 10: a backlog that grows shows here.
            tagged = [(w, w - (origin + e)) for (w, f, s, e) in lines if f == (k == "final")]
            t_first, t_last = tagged[0][0], tagged[-1][0]
            out[k]["first10_p90_ms"] = pct([l for w, l in tagged if w < t_first + 600_000], 0.9)
            out[k]["last10_p90_ms"] = pct([l for w, l in tagged if w > t_last - 600_000], 0.9)
    return out


def frame_stats(log):
    rows = [dict((k, float(v)) for k, v in re.findall(r"(\w+)=([\d.]+)", m.group(0)))
            for m in re.finditer(r"by frames[^\n]*", log)]
    rows = [r for r in rows if r.get("n", 0) > 0]
    if not rows:
        return None
    n = sum(r["n"] for r in rows)
    return {"windows": len(rows), "frames": n, "p50_ms_median": statistics.median(r["p50"] for r in rows),
            "p95_ms_worst": max(r["p95"] for r in rows), "p95_ms_median": statistics.median(r["p95"] for r in rows),
            "max_ms": max(r["max"] for r in rows), "over8_pct": 100 * sum(r["over8"] for r in rows) / n,
            "over16_pct": 100 * sum(r["over16"] for r in rows) / n}


def cmd_meeting(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    loops = max(1, round(a.mins * 60_000 / 120_000))
    mic, sysw, dur = build_fixture(Path(a.fix).resolve(), out.resolve() / "fixture", loops)
    name = a.name or f"meeting-{a.engine}-{a.mins}m"
    r = profile(out / "runs" / name, True, a.models, a.engine, stub_port=8899)
    script = r / "mic_script.txt"
    script.write_text("".join(f"{3000 + k * dur} {mic}\n" for k in range(loops)))
    secs = loops * dur // 1000 + 8
    st = stub(8899, r / "stub.jsonl")
    extra = {"CAP_GPUI_AUTO_MEETING": str(secs), "CAP_GPUI_AUTO_MEETING_LINGER": str(a.linger),
             "CAP_GPUI_AUTO_MEETING_OUT": str(r / "summary.json"), "CAP_GPUI_AUTO_MEETING_MIC_SCRIPT": str(script),
             "CAP_GPUI_AUTO_MEETING_MARKS": str(r / "marks.txt"), "CAP_GPUI_DOCS_OPEN": "live", "CAP_BY_FRAMES": "1"}
    p, t0 = spawn(a.pkg, r, extra)
    smp = Sampler(p.pid, 5.0); smp.start()
    started = None
    for _ in range(3000):
        if "CAP_GPUI_AUTO_MEETING: recording" in applog(r) or p.poll() is not None:
            started = time.time()
            break
        time.sleep(0.1)
    time.sleep(3)
    play = player(sysw)
    energy = Energy(r / "powermetrics.txt", secs + a.linger, interval=30)
    deadline = time.time() + secs + a.linger + 1800
    while time.time() < deadline and p.poll() is None and not (r / "summary.json").exists():
        time.sleep(2)
    time.sleep(2)
    smp.stop.set(); smp.join()
    e = energy.finish()
    for q in (play, st):
        q.terminate()
    kill(p)
    log = applog(r)
    (r / "app.log").write_text(log)
    summ = json.loads((r / "summary.json").read_text()) if (r / "summary.json").exists() else {}
    rows = smp.rows
    with open(r / "samples.jsonl", "w") as f:
        for x in rows:
            f.write(json.dumps(x) + "\n")
    stop = re.search(r"by stop.*?wall=(\d+)", log)
    stop = int(stop.group(1)) if stop else None
    drained = re.search(r"CAP_GPUI_AUTO_MEETING: drained.*?drain_ms=(\d+)", log)
    notes = {m.group(1): int(m.group(2)) for m in re.finditer(r"by notes.*?stage=\"?(\w+)\"?.*?wall=(\d+)", log)}
    rec = [x for x in rows if started and stop and started + 30 <= x["t"] <= stop / 1000]
    mb = lambda x: x / 1048576
    tenths = []
    if rec:
        k = max(1, len(rec) // 6)
        tenths = [round(mb(statistics.mean(x["rss"] for x in rec[i:i + k])), 1) for i in range(0, len(rec), k)]
    before = (summ.get("beforeStop") or {}).get("active") or {}
    words = sum(len(u["text"].split()) for u in summ.get("utterances", []) if u.get("final"))
    row = {"cmd": "meeting", "name": name, "engine": a.engine, "mins": a.mins, "summary_engine": summ.get("engine"),
           "rec_cpu_pct": (rate(rec, "cpu") or 0) * 100, "rec_ctx_s": rate(rec, "ctx"), "rec_wakeups_s": rate(rec, "wakeups"),
           "rec_disk_w_kb_s": (rate(rec, "disk_w") or 0) / 1024,
           "rss_mb_by_sixth": tenths, "rss_mb_max": mb(max(x["rss"] for x in rows)) if rows else None,
           "rss_mb_end": mb(rows[-1]["rss"]) if rows else None,
           "footprint_mb_max": mb(max(x["footprint"] for x in rows)) if rows else None,
           "disk_w_mb_total": mb(rows[-1]["disk_w"] - rows[0]["disk_w"]) if rows else None,
           "captions": caption_stats(log), "live_frames": frame_stats(log),
           "drain_ms": int(drained.group(1)) if drained else None,
           "stop_to_notes_started_ms": notes.get("started") - stop if stop and "started" in notes else None,
           "stop_to_notes_done_ms": notes.get("done") - stop if stop and "done" in notes else None,
           "notes_failed": "failed" in notes,
           "dropped": {k: (before.get(k) or {}).get(x) for k in ("mic", "system") for x in ("droppedBatches", "droppedSegments")} if before else None,
           "drops": {f"{k}_{x}": (before.get(k) or {}).get(x) for k in ("mic", "system") for x in ("droppedBatches", "droppedSegments")},
           "health": {k: (before.get(k) or {}).get("health") for k in ("mic", "system")},
           "final_words": words, "lines": summ.get("utteranceCount"), "cpu_s_app": summ.get("cpuSeconds"),
           "peak_rss_mb_app": mb(summ.get("peakRssBytes") or 0), "energy": e,
           "panics": len(re.findall(r"panicked", log)), "errors": len(re.findall(r" ERROR ", log))}
    row.pop("dropped")
    append(out, "meeting", row)


def corpus(work, minutes=180):
    work = Path(work); work.mkdir(parents=True, exist_ok=True)
    t, n = work / "long.txt", work / "long.md"
    if not t.exists():
        words = ("we should ship the onboarding change after the pricing review then check the numbers with "
                 "finance and loop in design on the new flow before the launch next week").split()
        people = ["Ana Ruiz", "Ben Okafor", "Chloe Martin", "Dev Patel"]
        total, i, lines = 0, 0, []
        while total < minutes * 60_000:
            k = 8 + (i * 7) % 18
            text = " ".join(words[(i + j) % len(words)] for j in range(k))
            lines.append(f"{people[i % 4]}: {text.capitalize()}.")
            total += max(900, k * 330) + 400
            i += 1
        t.write_text("\n".join(lines) + "\n")
        n.write_text("Title: Three hour planning offsite\n## Summary\n- Long planning session.\n")
    return t, n


def cmd_ui(a):
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    t, n = corpus(out.resolve() / "corpus")
    r = profile(out / "runs" / "ui", True)
    for run in range(a.runs):
        rr = out / "runs" / f"ui-{run}"; rr.mkdir(parents=True, exist_ok=True)
        extra = {"CAP_GPUI_DOCS_OPEN": "1", "CAP_GPUI_DOCS_SEED": str(a.seed), "CAP_GPUI_DOCS_CORPUS": f"{t}|{n}",
                 "CAP_GPUI_DOCS_STATE": "select:Three hour planning offsite,wait:1500",
                 "CAP_GPUI_DOCS_BENCH": "list,page_scroll,draw,switch_frames,switch_long,search,scroll,list",
                 "CAP_GPUI_DOCS_QUIT": "1"}
        (r / "app.out").unlink(missing_ok=True)
        p, t0 = spawn(a.pkg, r, extra)
        smp = Sampler(p.pid, 1.0); smp.start()
        try:
            p.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            pass
        smp.stop.set(); smp.join()
        kill(p)
        log = applog(r)
        (rr / "app.log").write_text(log)
        benches = {}
        for m in re.finditer(r"documents-bench: (\S+) ([\d.]+) mean / ([\d.]+) p50 / ([\d.]+) p95 / ([\d.]+) max us", log):
            benches[m.group(1)] = {"mean_ms": float(m.group(2)) / 1000, "p50_ms": float(m.group(3)) / 1000,
                                   "p95_ms": float(m.group(4)) / 1000, "max_ms": float(m.group(5)) / 1000}
        listing = re.findall(r"documents-bench: listing Some\((\d+)\) ms", log)
        seeded = re.search(r"documents seeded.*?ms=(\d+)", log)
        row = {"cmd": "ui", "run": run, "seeded_ms": int(seeded.group(1)) if seeded else None,
               "listing_ms": [int(x) for x in listing], "benches": benches,
               "rss_mb_max": max(x["rss"] for x in smp.rows) / 1048576 if smp.rows else None,
               "elapsed_s": round(time.time() - t0), "panics": len(re.findall(r"panicked", log))}
        append(out, "ui", row)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("launch", "idle", "meeting", "ui"):
        s = sub.add_parser(name)
        s.add_argument("--out", required=True)
        s.add_argument("--models")
    sub.choices["launch"].add_argument("--arm", action="append", required=True)
    sub.choices["launch"].add_argument("--rounds", type=int, default=7)
    sub.choices["launch"].add_argument("--hold", type=float, default=8.0)
    sub.choices["idle"].add_argument("--arm", action="append", required=True)
    sub.choices["idle"].add_argument("--rounds", type=int, default=2)
    sub.choices["idle"].add_argument("--settle", type=int, default=45)
    sub.choices["idle"].add_argument("--secs", type=int, default=180)
    m = sub.choices["meeting"]
    m.add_argument("--pkg", required=True); m.add_argument("--fix", required=True)
    m.add_argument("--mins", type=int, default=60); m.add_argument("--engine", default="default")
    m.add_argument("--linger", type=int, default=90); m.add_argument("--name")
    u = sub.choices["ui"]
    u.add_argument("--pkg", required=True); u.add_argument("--runs", type=int, default=3)
    u.add_argument("--seed", type=int, default=500)
    a = ap.parse_args()
    {"launch": cmd_launch, "idle": cmd_idle, "meeting": cmd_meeting, "ui": cmd_ui}[a.cmd](a)


if __name__ == "__main__":
    main()
