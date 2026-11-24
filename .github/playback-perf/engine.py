#!/usr/bin/env python3
"""Engine playback benchmark A/B: runs each arm's editor-playback-benchmark.

engine.py --bins <dir with arms.txt> --fixture <project.cap> --out <dir>
          [--rounds 3] [--frames 600] [--resolutions half,full] [--extra "--bgra-surface"]

Prints, per arm and resolution, the medians of the benchmark's own numbers:
process CPU, effective fps, skips, and avg/p95/max of each stage.
"""

import argparse
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path

STAGE = re.compile(r"^\s{2}(?P<name>[a-z][a-z ]+?)\s+avg=\s*(?P<avg>[\d.]+)ms p95=\s*(?P<p95>[\d.]+)ms max=\s*(?P<max>[\d.]+)ms samples=(?P<n>\d+)")
CPU = re.compile(r"Process CPU: ([\d.]+)s over ([\d.]+)s \((\d+)% of one core\)")
FPS = re.compile(r"Effective FPS: submitted=([\d.]+) rendered=([\d.]+)")
FRAMES = re.compile(r"Frames: submitted=(\d+) rendered=(\d+) callback=(\d+) renderer_dropped=(\d+) skipped=(\d+)")
WARMUP = re.compile(r"Warmup: ([\d.]+)ms")


def run(binary, fixture, frames, resolution, extra, lib_dir, log):
    env = dict(os.environ, NO_COLOR="1")
    if lib_dir:
        key = {"darwin": "DYLD_FALLBACK_LIBRARY_PATH", "win32": "PATH"}.get(sys.platform, "LD_LIBRARY_PATH")
        env[key] = lib_dir + os.pathsep + env.get(key, "")
    command = [str(binary), "--recording-path", str(fixture), "--fps", "60", "--frames", str(frames),
               "--resolution", resolution, "--headless", *extra]
    try:
        done = subprocess.run(command, env=env, capture_output=True, text=True, errors="replace", timeout=600)
        text = done.stdout + "\n" + done.stderr
        code = done.returncode
    except subprocess.TimeoutExpired as error:
        text = (error.stdout or "") + "\nTIMEOUT"
        code = "timeout"
    log.write_text(text, encoding="utf-8")
    result = {"exit": code, "stages": {}}
    for line in text.splitlines():
        if match := CPU.search(line):
            result["cpu_pct"] = float(match[3])
        elif match := FPS.search(line):
            result["fps_rendered"] = float(match[2])
        elif match := FRAMES.search(line):
            result["dropped"] = int(match[4])
            result["skipped"] = int(match[5])
        elif match := WARMUP.search(line):
            result["warmup_ms"] = float(match[1])
        elif match := STAGE.search(line):
            result["stages"].setdefault(match["name"].strip(), (float(match["avg"]), float(match["p95"]), float(match["max"])))
    return result


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def fmt(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bins", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--frames", type=int, default=600)
    parser.add_argument("--resolutions", default="half,full")
    parser.add_argument("--extra", default="")
    parser.add_argument("--lib-dir", default="")
    parser.add_argument("--label", default=sys.platform)
    parser.add_argument("--summary")
    args = parser.parse_args()

    bins = Path(args.bins)
    exe = ".exe" if sys.platform == "win32" else ""
    arms = [line.split()[0] for line in (bins / "arms.txt").read_text().splitlines() if line.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    extra = args.extra.split()
    rows = {}
    first = bins / arms[0] / f"editor-playback-benchmark{exe}"
    run(first, args.fixture, 120, "half", extra, args.lib_dir, out / "warm.log")
    for resolution in args.resolutions.split(","):
        for round_index in range(args.rounds):
            order = arms if round_index % 2 == 0 else list(reversed(arms))
            for arm in order:
                binary = bins / arm / f"editor-playback-benchmark{exe}"
                result = run(binary, args.fixture, args.frames, resolution, extra, args.lib_dir,
                             out / f"{resolution}-{arm}-{round_index}.log")
                rows.setdefault((resolution, arm), []).append(result)
                print(f"{resolution} round {round_index} {arm}: exit {result['exit']} cpu {result.get('cpu_pct')}% "
                      f"fps {result.get('fps_rendered')} skipped {result.get('skipped')} dropped {result.get('dropped')}", flush=True)

    lines = [f"## Engine playback benchmark: {args.label} ({args.frames} frames, {args.rounds} rounds, extra '{args.extra}')", ""]
    for resolution in args.resolutions.split(","):
        lines.append(f"### {resolution}")
        lines.append("| arm | CPU % of a core (min..max) | rendered fps | skipped | renderer dropped | warmup ms |")
        lines.append("|---|---|---|---|---|---|")
        for arm in arms:
            runs = rows[(resolution, arm)]
            cpu = [r.get("cpu_pct") for r in runs if r.get("cpu_pct") is not None]
            spread = f"{fmt(median(cpu), 0)} ({min(cpu):.0f}..{max(cpu):.0f})" if cpu else "-"
            lines.append(f"| {arm} | {spread} | {fmt(median([r.get('fps_rendered') for r in runs]), 1)} | "
                         f"{[r.get('skipped') for r in runs]} | {[r.get('dropped') for r in runs]} | {fmt(median([r.get('warmup_ms') for r in runs]), 0)} |")
        names = []
        for arm in arms:
            for r in rows[(resolution, arm)]:
                for name in r["stages"]:
                    if name not in names:
                        names.append(name)
        lines.append("")
        lines.append("| stage avg / p95 / max ms | " + " | ".join(arms) + " |")
        lines.append("|---|" + "---|" * len(arms))
        for name in names:
            cells = []
            for arm in arms:
                values = [r["stages"][name] for r in rows[(resolution, arm)] if name in r["stages"]]
                cells.append(f"{fmt(median([v[0] for v in values]))} / {fmt(median([v[1] for v in values]))} / {fmt(median([v[2] for v in values]))}")
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines.append("")
    text = "\n".join(lines)
    (out / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


if __name__ == "__main__":
    main()
