"""Send the same fault-or-weather cases to an ordinary chat LLM (OpenAI-compatible endpoint)."""
import json, os, re, statistics, sys, time, urllib.request
DEEP = os.environ.get("THINK") == "on"
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
S = Path(sys.argv[1]); ENV = Path(sys.argv[2]); MODEL = sys.argv[3] if len(sys.argv) > 3 else None
sys.path.insert(0, str(S)); import eval_kev_lib as base
cfg = dict(line.split("=", 1) for line in ENV.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
KEY = cfg["MODEL_API_KEY_ENV"].strip(); URL = cfg["MODEL_BASE_URL"].strip().rstrip("/") + "/chat/completions"; MODEL = MODEL or cfg["MODEL_NAME"].strip()
nodes = json.loads((S / "k4" / "nodes_scored_kev4b.json").read_text())
SYSTEM = ("You monitor a survey telescope. " + base.RULES +
          "Decide whether a hidden instrument fault is the cause of the low throughput now.")
def chat(user, think):
    tail = ('Think step by step in a few sentences, then end with a line: PROBABILITY: <number between 0 and 1>' if think
            else 'Answer with only one line: PROBABILITY: <number between 0 and 1>')
    body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user + "\n\n" + tail}], "max_tokens": 16000 if DEEP else 600}
    if not DEEP: body["thinking"] = {"type": "disabled"}; body["temperature"] = 0
    for attempt in range(4):
        try:
            req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"content-type": "application/json", "authorization": "Bearer " + KEY})
            t = time.perf_counter()
            with urllib.request.urlopen(req, timeout=400 if DEEP else 90) as r: out = json.load(r)
            text = out["choices"][0]["message"]["content"] or ""
            m = re.findall(r"PROBABILITY:\s*([01](?:\.\d+)?)", text)
            return (float(m[-1]) if m else None), (time.perf_counter() - t) * 1000, text
        except Exception as error:  # noqa: BLE001
            last = repr(error)[:200]; time.sleep(2 * (attempt + 1))
    return None, 0.0, "ERROR " + last
jobs = [(i, key, state, think) for i, n in enumerate(nodes) for key, state, think in
        ([("l_digest", base.digest(n), False), ("l_raw", base.raw(n), False)] if DEEP else
         [("l_digest", base.digest(n), False), ("l_raw", base.raw(n), False), ("l_raw_think", base.raw(n), True)])]
lat = {"l_digest": [], "l_raw": [], "l_raw_think": []}; samples = {}
def work(job):
    i, key, state, think = job
    p, ms, text = chat(state, think)
    nodes[i][key] = p; lat[key].append(ms)
    if key == "l_raw_think" and len(samples) < 2: samples[i] = text
started = time.perf_counter()
with ThreadPoolExecutor(16) as pool: list(pool.map(work, jobs))
(S / f"nodes_scored_llm_{MODEL}_{'think' if DEEP else 'direct'}.json").write_text(json.dumps(nodes))
print(f"model {MODEL} ({'built-in reasoning on' if DEEP else 'built-in reasoning off'}): {len(jobs)} calls in {time.perf_counter() - started:.0f} s, 16 at a time")
def auc(key):
    ok = [n for n in nodes if n.get(key) is not None]
    pos = [n[key] for n in ok if n['label']]; neg = [n[key] for n in ok if not n['label']]
    return (sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')), len(nodes) - len(ok)
for key, name in [("l_digest", "digest + rules"), ("l_raw", "raw numbers + rules, direct answer")] + ([] if DEEP else [("l_raw_think", "raw numbers + rules, reasoning first")]) + [("r_agent_after", "baseline's flag")]:
    a, missing = auc(key)
    ok = [n for n in nodes if n.get(key) is not None]
    P = [n for n in ok if n['label']]; N = [n for n in ok if not n['label']]
    tp = sum(1 for n in P if n[key] >= 0.5); fp = sum(1 for n in N if n[key] >= 0.5)
    line = f"{name:38s} AUC {a:.3f}  at 0.5: caught {tp}/{len(P)}, false {fp}/{len(N)}  unparsed {missing}"
    if key in lat and lat[key]: line += f"  latency median {statistics.median(lat[key]):.0f} ms p90 {statistics.quantiles(lat[key], n=10)[8]:.0f} ms"
    print(line)
for key in ("l_digest", "l_raw" if DEEP else "l_raw_think"):
    print(key, "calibration:", "; ".join(f"{lo:.1f}-{hi:.1f}: n={len(b)} actual {statistics.mean(x['label'] for x in b):.2f}"
          for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)] for b in [[x for x in nodes if x.get(key) is not None and lo <= x[key] < hi]] if b))
for i, text in samples.items(): print(f"--- sample reasoning (truth: {'fault' if nodes[i]['label'] else 'not fault'}):\n{text[:700]}")
