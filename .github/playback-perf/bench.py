#!/usr/bin/env python3
"""Editor playback A/B: runs each binary's real editor on a project, interleaved.

bench.py --bin base=<cap-gpui> --bin head=<cap-gpui> --fixture ref=<project.cap>
         --out <dir> [--rounds 5] [--kinds play,edit] [--quality half] [--scale 1]

Per run: fresh profile and project copy, the app's own latency bench drives the
editor, and the process is sampled for CPU time and memory every 50 ms. CPU for
the playback window is read from those samples around the bench's own lines.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

ANSI = re.compile(r"\x1b\[[0-9;]*m")
PLAYBACK = re.compile(
    r"latency-bench: playback first-frame (?P<first>[\d.-]+) ms, (?P<fps>[\d.]+) fps presented over "
    r"(?P<secs>\d+)s, (?P<skipped>\d+) source frames skipped, (?P<repeats>\d+) out of order, "
    r"(?P<late>\d+) intervals over 25 ms, (?P<replaced>\d+) replaced before paint, pump dropped (?P<dropped>\d+)"
)
STAGE = re.compile(
    r"latency-bench: (?P<label>\S+) (?P<stage>.+?) n=(?P<n>\d+)(?: p50 (?P<p50>[\d.]+) / p95 (?P<p95>[\d.]+) / "
    r"p99 (?P<p99>[\d.]+) / max (?P<max>[\d.]+) ms)?"
)
INTERACTION = re.compile(
    r"latency-bench: (?P<label>\S+) (?P<inputs>\d+) inputs, (?P<asked>\d+) asked for a frame, (?P<shown>\d+) shown, "
    r"(?P<superseded>\d+) superseded, (?P<presentations>\d+) presentations \((?P<stale>\d+) stale\), "
    r"(?P<replaced>\d+) replaced before paint"
)
MEMORY = re.compile(r"latency-bench: memory after (?P<label>\S+) gpu (?P<gpu>[\d.-]+)(?: MB)?, footprint (?P<fp>[\d.-]+)")
ENGINE = re.compile(r"Playback stats")
FIELD = re.compile(r"(\w+)=\"?([\d.]+)\"?")

STORE = {
    "general_settings": {
        "hasCompletedOnboarding": True,
        "hasCompletedStartup": True,
        "enableNotifications": False,
        "enableTelemetry": False,
        "recordingCountdown": 0,
    },
    "recording_start_safety": {"confirmBeforeRecordingWithoutMicrophone": False},
}

KINDS = {
    "play": "playback",
    "edit": "scrub-fast,scrub-fine,zoom-drag,slider,pause",
}
PLAYBACK_SECS = float(os.environ.get("BENCH_PLAYBACK_SECS", "10"))
SETTLE_SECS = 0.6


def copy_project(source: Path, target: Path):
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if path.is_dir():
            (target / relative).mkdir(parents=True, exist_ok=True)
            continue
        (target / relative).parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in (".json", ".log") or path.stat().st_size < 65536:
            shutil.copyfile(path, target / relative)
        else:
            try:
                os.link(path, target / relative)
            except OSError:
                shutil.copyfile(path, target / relative)


def sample(process, samples, stop):
    if psutil is None:
        return
    try:
        handle = psutil.Process(process.pid)
    except psutil.Error:
        return
    while not stop.is_set():
        try:
            cpu = handle.cpu_times()
            memory = handle.memory_info()
            samples.append((time.monotonic(), cpu.user + cpu.system, memory.rss))
        except psutil.Error:
            break
        stop.wait(0.05)


def cpu_between(samples, start, end):
    inside = [s for s in samples if start <= s[0] <= end]
    if len(inside) < 2:
        return None
    return inside[-1][1] - inside[0][1]


def run_once(binary: Path, fixture: Path, scratch: Path, kind: str, args, frame_png=None):
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    profile = scratch / "profile"
    for sub in ("recordings", "logs"):
        (profile / sub).mkdir(parents=True)
    store = json.loads(json.dumps(STORE))
    if args.quality:
        store["general_settings"]["editorPreviewQuality"] = args.quality
        store["general_settings"]["editorPreviewQualityChosen"] = True
    (profile / "store").write_text(json.dumps(store), encoding="utf-8")
    project = scratch / "project.cap"
    copy_project(fixture, project)
    env = dict(
        os.environ,
        CAP_GPUI_APP_DATA_DIR=str(profile),
        CAP_GPUI_RECORDINGS_DIR=str(profile / "recordings"),
        CAP_GPUI_LOG_DIR=str(profile / "logs"),
        CAP_GPUI_AUTO_NO_MIC="1",
        CAP_GPUI_AUTO_EDITOR=str(project),
        CAP_GPUI_AUTO_LATENCY_BENCH="1",
        CAP_GPUI_AUTO_LATENCY_SCENARIOS=KINDS[kind],
        CAP_GPUI_AUTO_LATENCY_BACKING_SCALE=str(args.scale),
        CAP_GPUI_NO_MENUS="1",
        CAP_GPUI_NO_TRAY="1",
        CAP_GPUI_MUTE_AUDIO="1",
        RUST_LOG="warn,cap_gpui=info,cap_desktop_gpui=info,cap_editor=info,cap_rendering=info",
        NO_COLOR="1",
    )
    if frame_png:
        env["CAP_GPUI_AUTO_LATENCY_FRAME"] = str(frame_png)
    for pair in args.env or []:
        key, _, value = pair.partition("=")
        env[key] = value
    if args.lib_dir:
        key = {"darwin": "DYLD_FALLBACK_LIBRARY_PATH", "win32": "PATH"}.get(sys.platform, "LD_LIBRARY_PATH")
        env[key] = args.lib_dir + os.pathsep + env.get(key, "")

    log_path = scratch / "app.log"
    lines = []
    samples = []
    stop = threading.Event()
    started = time.monotonic()
    process = subprocess.Popen(
        [str(binary)],
        cwd=str(binary.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        bufsize=1,
    )
    sampler = threading.Thread(target=sample, args=(process, samples, stop), daemon=True)
    sampler.start()

    def reader():
        with open(log_path, "w", encoding="utf-8") as log:
            for raw in process.stdout:
                line = ANSI.sub("", raw.rstrip("\n"))
                lines.append((time.monotonic(), line))
                log.write(line + "\n")

    read_thread = threading.Thread(target=reader, daemon=True)
    read_thread.start()
    timed_out = False
    try:
        process.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    stop.set()
    read_thread.join(timeout=10)
    sampler.join(timeout=2)
    wall = time.monotonic() - started

    result = {
        "exit": process.returncode,
        "timed_out": timed_out,
        "wall": round(wall, 2),
        "cpu_total": round(samples[-1][1], 3) if samples else None,
        "rss_peak_mb": round(max(s[2] for s in samples) / 1048576, 1) if samples else None,
        "stages": {},
        "interactions": {},
        "memory": {},
        "engine": {},
        "notes": [],
    }
    for at, line in lines:
        match = PLAYBACK.search(line)
        if match:
            fields = match.groupdict()
            result["playback"] = {
                "first_frame_ms": None if fields["first"] == "-" else float(fields["first"]),
                "fps": float(fields["fps"]),
                "skipped": int(fields["skipped"]),
                "out_of_order": int(fields["repeats"]),
                "late": int(fields["late"]),
                "replaced": int(fields["replaced"]),
                "pump_dropped": int(fields["dropped"]),
            }
            end = at - SETTLE_SECS
            cpu = cpu_between(samples, end - PLAYBACK_SECS, end)
            if cpu is not None:
                result["playback"]["cpu_secs"] = round(cpu, 3)
                result["playback"]["cpu_cores"] = round(cpu / PLAYBACK_SECS, 4)
            inside = [s[2] for s in samples if end - PLAYBACK_SECS <= s[0] <= end]
            if inside:
                result["playback"]["rss_peak_mb"] = round(max(inside) / 1048576, 1)
                result["playback"]["rss_mean_mb"] = round(statistics.mean(inside) / 1048576, 1)
            continue
        match = INTERACTION.search(line)
        if match:
            fields = match.groupdict()
            label = fields.pop("label")
            result["interactions"][label] = {k: int(v) for k, v in fields.items()}
            continue
        match = MEMORY.search(line)
        if match:
            result["memory"][match["label"]] = {
                "gpu_mb": None if match["gpu"] == "-" else float(match["gpu"]),
                "footprint_mb": None if match["fp"] == "-" else float(match["fp"]),
            }
            continue
        match = STAGE.search(line)
        if match and "inputs," not in line and "memory after" not in line and match["stage"] != "playback first-frame":
            key = f"{match['label']} {match['stage']}"
            if match["p50"] is not None:
                result["stages"][key] = {
                    "n": int(match["n"]),
                    "p50": float(match["p50"]),
                    "p95": float(match["p95"]),
                    "p99": float(match["p99"]),
                    "max": float(match["max"]),
                }
            continue
        if ENGINE.search(line):
            result["engine"] = {k: float(v) for k, v in FIELD.findall(line)}
            continue
        if "latency-bench: viewport" in line or "pause round" in line:
            result["notes"].append(line.split("latency-bench: ", 1)[-1])
        elif any(word in line for word in ("panicked", "adapter", "shared wgpu device", "editor frame size", "decoder")) and len(result["notes"]) < 40:
            result["notes"].append(line[:240])
    return result


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def summarise(rows, bins, fixtures, kinds):
    out = []
    for fixture in fixtures:
        if "play" in kinds:
            out.append(f"### {fixture}: playback (median of runs, min..max)")
            out.append("| build | runs | fps | skipped | out of order | late >25ms | replaced | pump dropped | interval p50/p95/p99/max ms | first frame ms | CPU cores | RSS peak MB | footprint MB | engine skipped |")
            out.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for label in bins:
                runs = [r for r in rows if r["bin"] == label and r["fixture"] == fixture and r["kind"] == "play" and "playback" in r]
                if not runs:
                    out.append(f"| {label} | 0 | no result |")
                    continue
                play = [r["playback"] for r in runs]
                interval = [r["stages"].get("playback present interval", {}) for r in runs]

                def span(key, source=play, digits=1):
                    values = [p.get(key) for p in source if p.get(key) is not None]
                    if not values:
                        return "-"
                    return f"{statistics.median(values):.{digits}f} ({min(values):.{digits}f}..{max(values):.{digits}f})"

                footprint = [r["memory"].get("playback", {}).get("footprint_mb") for r in runs]
                engine = [r["engine"].get("total_skipped") for r in runs]
                out.append(
                    f"| {label} | {len(runs)} | {span('fps')} | {span('skipped', digits=0)} | {span('out_of_order', digits=0)} | "
                    f"{span('late', digits=0)} | {span('replaced', digits=0)} | {span('pump_dropped', digits=0)} | "
                    f"{fmt(median([i.get('p50') for i in interval]))}/{fmt(median([i.get('p95') for i in interval]))}/"
                    f"{fmt(median([i.get('p99') for i in interval]))}/{fmt(median([i.get('max') for i in interval]))} | "
                    f"{span('first_frame_ms')} | {span('cpu_cores', digits=3)} | {span('rss_peak_mb')} | "
                    f"{fmt(median(footprint), 1)} | {fmt(median(engine), 0)} |"
                )
            stages = sorted({k for r in rows if r["fixture"] == fixture and r["kind"] == "play" for k in r["stages"] if k != "playback present interval"})
            if stages:
                out.append("")
                out.append("| stage (p50 / p95 ms, median of runs) | " + " | ".join(bins) + " |")
                out.append("|---|" + "---|" * len(bins))
                for stage in stages:
                    cells = []
                    for label in bins:
                        values = [r["stages"][stage] for r in rows if r["bin"] == label and r["fixture"] == fixture and r["kind"] == "play" and stage in r["stages"]]
                        cells.append(f"{fmt(median([v['p50'] for v in values]))} / {fmt(median([v['p95'] for v in values]))}")
                    out.append(f"| {stage} | " + " | ".join(cells) + " |")
            out.append("")
        if "edit" in kinds:
            out.append(f"### {fixture}: interactions (p50 / p95 ms, median of runs)")
            stages = sorted({k for r in rows if r["fixture"] == fixture and r["kind"] == "edit" for k in r["stages"]})
            out.append("| stage | " + " | ".join(bins) + " |")
            out.append("|---|" + "---|" * len(bins))
            for stage in stages:
                cells = []
                for label in bins:
                    values = [r["stages"][stage] for r in rows if r["bin"] == label and r["fixture"] == fixture and r["kind"] == "edit" and stage in r["stages"]]
                    cells.append(f"{fmt(median([v['p50'] for v in values]))} / {fmt(median([v['p95'] for v in values]))} (n={len(values)})")
                out.append(f"| {stage} | " + " | ".join(cells) + " |")
            for label in bins:
                runs = [r for r in rows if r["bin"] == label and r["fixture"] == fixture and r["kind"] == "edit"]
                cpu = median([r["cpu_total"] for r in runs])
                rss = median([r["rss_peak_mb"] for r in runs])
                stale = [sum(i.get("stale", 0) for i in r["interactions"].values()) for r in runs]
                out.append(f"- {label}: {len(runs)} runs, CPU total {fmt(cpu)} s, RSS peak {fmt(rss, 1)} MB, stale presentations {stale}, exits {[r['exit'] for r in runs]}")
            out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bin", action="append", required=True, help="label=path")
    parser.add_argument("--fixture", action="append", required=True, help="name=path")
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--kinds", default="play")
    parser.add_argument("--quality", default="")
    parser.add_argument("--scale", default="1")
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--lib-dir", default="")
    parser.add_argument("--env", action="append")
    parser.add_argument("--label", default=sys.platform)
    parser.add_argument("--summary")
    parser.add_argument("--no-prime", action="store_true")
    parser.add_argument("--frames", action="store_true", help="save each edit run's settled paused frame")
    args = parser.parse_args()

    bins = dict(pair.split("=", 1) for pair in args.bin)
    fixtures = dict(pair.split("=", 1) for pair in args.fixture)
    kinds = args.kinds.split(",")
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / "scratch"
    rows = []
    results = out / "results.jsonl"

    if not args.no_prime:
        # The first open of a recording repairs its tracks in place, and the
        # first launch of a build compiles its shaders.
        first = next(iter(bins.values()))
        for name, fixture in fixtures.items():
            started = time.monotonic()
            saved = args.timeout
            args.timeout = max(saved, 600)
            profile_run = prime(Path(first), Path(fixture), scratch, args)
            args.timeout = saved
            print(f"primed {name}: exit {profile_run} in {time.monotonic() - started:.1f}s", flush=True)
        for label, binary in bins.items():
            fixture = next(iter(fixtures.values()))
            result = run_once(Path(binary), Path(fixture), scratch, "play", args)
            print(f"warm {label}: exit {result['exit']} {json.dumps(result.get('playback'))}", flush=True)
            for note in result["notes"][:12]:
                print(f"  {note}", flush=True)

    for round_index in range(args.rounds):
        order = list(bins.items())
        if round_index % 2 == 1:
            order.reverse()
        for name, fixture in fixtures.items():
            for kind in kinds:
                for label, binary in order:
                    frame = out / f"frame-{name}-{label}-{round_index}.png" if args.frames and kind == "edit" else None
                    result = run_once(Path(binary), Path(fixture), scratch, kind, args, frame)
                    result.update(bin=label, fixture=name, kind=kind, round=round_index)
                    rows.append(result)
                    with results.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(result) + "\n")
                    shutil.copyfile(scratch / "app.log", out / f"{name}-{kind}-{label}-{round_index}.log")
                    print(f"round {round_index} {name} {kind} {label}: exit {result['exit']} wall {result['wall']}s cpu {result['cpu_total']} {json.dumps(result.get('playback', {}))}", flush=True)
    shutil.rmtree(scratch, ignore_errors=True)
    text = f"## Editor playback A/B: {args.label} (quality {args.quality or 'default'}, scale {args.scale}, {args.rounds} rounds)\n\n" + summarise(rows, list(bins), list(fixtures), kinds)
    (out / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    broken = [r for r in rows if r["exit"] != 0 or r["timed_out"]]
    if broken:
        print(f"::error::{len(broken)} runs did not exit cleanly")
        sys.exit(1)


def prime(binary: Path, fixture: Path, scratch: Path, args):
    """Opens the fixture itself (not a copy) so its one-time repair is done."""
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    profile = scratch / "profile"
    for sub in ("recordings", "logs"):
        (profile / sub).mkdir(parents=True)
    (profile / "store").write_text(json.dumps(STORE), encoding="utf-8")
    env = dict(
        os.environ,
        CAP_GPUI_APP_DATA_DIR=str(profile),
        CAP_GPUI_RECORDINGS_DIR=str(profile / "recordings"),
        CAP_GPUI_LOG_DIR=str(profile / "logs"),
        CAP_GPUI_AUTO_NO_MIC="1",
        CAP_GPUI_AUTO_EDITOR=str(fixture),
        CAP_GPUI_AUTO_LATENCY_BENCH="1",
        CAP_GPUI_AUTO_LATENCY_SCENARIOS="scrub-fine",
        CAP_GPUI_NO_MENUS="1",
        CAP_GPUI_NO_TRAY="1",
        CAP_GPUI_MUTE_AUDIO="1",
        NO_COLOR="1",
    )
    if args.lib_dir:
        key = {"darwin": "DYLD_FALLBACK_LIBRARY_PATH", "win32": "PATH"}.get(sys.platform, "LD_LIBRARY_PATH")
        env[key] = args.lib_dir + os.pathsep + env.get(key, "")
    with open(scratch / "prime.log", "w", encoding="utf-8") as log:
        try:
            return subprocess.run([str(binary)], cwd=str(binary.parent), env=env, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            return "timeout"


if __name__ == "__main__":
    main()
