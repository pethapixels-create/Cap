"""bi_facts.py <run dir> <engine>: what one gotcha meeting produced, as facts for the progress file.
Reads <run>/doc (a copy of the saved document), summary.json, stub.jsonl and app.log."""
import collections, json, os, re, sys

R, ENGINE = sys.argv[1], sys.argv[2]
DOC = os.path.join(R, "doc")


def load(p, default=None):
    try:
        return json.load(open(p))
    except Exception:
        return default


summary = load(os.path.join(R, "summary.json"), {})
meta = load(os.path.join(DOC, "doc.json"), {})
m = meta.get("meeting") or {}
utt = {}
tpath = os.path.join(DOC, "transcript.jsonl")
bad_lines = 0
for line in open(tpath, errors="replace") if os.path.exists(tpath) else []:
    try:
        d = json.loads(line)
    except ValueError:
        bad_lines += 1
        continue
    if d.get("op") == "upsert":
        utt[d["utterance"]["id"]] = d["utterance"]
    elif d.get("op") == "delete":
        for i in d["ids"]:
            utt.pop(i, None)
final = [u for u in utt.values() if u.get("isFinal", True)]
words = collections.Counter()
cloud_words = collections.Counter()
for u in final:
    for w in (u.get("text") or "").split():
        words[u.get("source")] += 1
        if w.startswith("cloud"):
            cloud_words[w.strip(".,")] += 1
dupes = {w: c for w, c in cloud_words.items() if c > 1}
local_words = sum(words.values()) - sum(cloud_words.values())
stub = [json.loads(l) for l in open(os.path.join(R, "stub.jsonl"))] if os.path.exists(os.path.join(R, "stub.jsonl")) else []
paths = collections.Counter(e["path"].rsplit("/", 1)[-1] for e in stub)
faults = collections.Counter((e["path"].rsplit("/", 1)[-1], e.get("fault")) for e in stub if e.get("fault"))
seqs = collections.Counter((e.get("session"), e.get("seq")) for e in stub if e["path"].endswith("transcribe") and not e.get("fault"))
log = open(os.path.join(R, "app.log"), errors="replace").read() if os.path.exists(os.path.join(R, "app.log")) else ""
clean = re.sub(r"\x1b\[[0-9;]*m", "", log)
enhanced = load(os.path.join(DOC, "enhanced.json"))
enh_text = " ".join(b.get("text", "") for b in (enhanced or {}).get("blocks", [])) if isinstance(enhanced, dict) else ""
notes_ok = "Check the transcript against the script" in enh_text
interesting = collections.Counter()
for line in clean.splitlines():
    if re.search(r"\b(WARN|ERROR)\b|panicked", line):
        msg = re.sub(r"^\S+\s+(WARN|ERROR)\s+", r"\1 ", line)
        msg = re.sub(r"\d+", "N", msg)[:220]
        interesting[msg] += 1
run = open(os.path.join(R, "run.txt")).read() if os.path.exists(os.path.join(R, "run.txt")) else ""
print("facts", json.dumps({
    "title": meta.get("title"), "engine": m.get("engine"), "stopReason": m.get("stopReason"),
    "hasEnhanced": m.get("hasEnhanced"), "durationMs": m.get("durationMs"),
    "audio": [(a.get("file"), a.get("durationMs")) for a in m.get("audio") or []],
    "finalUtterances": len(final), "words": dict(words), "cloudWords": sum(cloud_words.values()),
    "localWords": local_words, "dupCloudWords": len(dupes), "badTranscriptLines": bad_lines,
    "stub": dict(paths), "faults": {f"{k[0]}:{k[1]}": v for k, v in faults.items()},
    "clipsAnsweredTwice": sum(1 for v in seqs.values() if v > 1),
    "notesSaved": notes_ok, "enhancedBlocks": len((enhanced or {}).get("blocks", [])) if isinstance(enhanced, dict) else None,
    "enhancedText": enh_text[:300], "summaryKeys": sorted(summary.keys()) if isinstance(summary, dict) else None,
    "summaryError": (summary or {}).get("error"), "drainMs": (summary or {}).get("drainMs"),
    "panics": clean.count("panicked"), "aliveAtEnd": "alive_at_end yes" in run,
}, ensure_ascii=False))
for msg, n in interesting.most_common(25):
    print(f"log {n}x {msg}")
