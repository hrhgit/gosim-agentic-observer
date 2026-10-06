"""Prompt optimisation for the fault-or-weather question on kimi-for-coding (low thinking effort).
Variants are compared on a 30-case development sample; the best one is then checked once on 40 fresh cases."""
import json, os, random, re, statistics, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
S = Path(sys.argv[1]); ENV = Path(sys.argv[2]); STAGE = sys.argv[3]; WHICH = sys.argv[4].split(",")
sys.path.insert(0, str(S)); import eval_kev_lib as base
key = next(l.split("=", 1)[1].strip().strip('"') for l in ENV.read_text().splitlines() if l.startswith("KIMI_API_KEY="))
hdr = tempfile.NamedTemporaryFile("w", suffix=".hdr", delete=False); os.chmod(hdr.name, 0o600)
hdr.write("authorization: Bearer " + key + "\ncontent-type: application/json\n"); hdr.close()
URL = "https://api.kimi.ai/coding/v1/chat/completions"
nodes = json.loads((S / "k4" / "nodes_scored_kev4b.json").read_text())
rng = random.Random(3)
P = [n for n in nodes if n["label"]]; N = [n for n in nodes if not n["label"]]
dev = rng.sample(P, 15) + rng.sample(N, 15)
ids = {(n["tag"], n["ts"]) for n in dev}
rest_p = [n for n in P if (n["tag"], n["ts"]) not in ids]; rest_n = [n for n in N if (n["tag"], n["ts"]) not in ids]
shots = [rest_p[0], rest_n[0], rest_n[1], rest_n[2]]            # worked examples, never used for scoring
shot_ids = {(n["tag"], n["ts"]) for n in shots}
rng2 = random.Random(5)
fresh = rng2.sample([n for n in rest_p if (n["tag"], n["ts"]) not in shot_ids and n["tag"] != shots[0]["tag"]], 20) + rng2.sample([n for n in rest_n if (n["tag"], n["ts"]) not in shot_ids], 20)
sample = dev if STAGE == "dev" else fresh

V1 = "You monitor a survey telescope. " + base.RULES + "Decide whether a hidden instrument fault is the cause of the low throughput now."
GUIDE = """You monitor a survey telescope and must decide whether a hidden instrument fault is the cause of the low throughput now.

How to read the table. Each line is one exposure. "throughput" is light received relative to nominal; "n/a" means that exposure gave no reading (ignore it). Times wrap at midnight and a jump of many hours means a new night. band_pass and band_fail count individual targets that confirmed or contradicted the declared sky band in that exposure: the size of band_pass depends on which field was observed and is NOT evidence of anything. Only band_fail matters, and only when band_pass is 0 in the same line. no_signal counts targets with no light at all.

Facts.
1. An instrument fault multiplies throughput by a fixed factor between 0.3 and 0.7 at one instant and stays. If that instant falls inside an exposure, that exposure reads in between, so a fault looks like: steady level, at most ONE intermediate reading, then a new steady level at 30-70% of the old one that repeats to within a few percent. It is never announced.
2. Weather drifts: successive readings keep changing by 3-15% each, often over many exposures, with no flat lower plateau. A drop of less than about 25% in total is almost always weather.
3. A reading below about 25% of the earlier level, or several no_signal targets, is a site closure or terrain, not a fault.
4. If a bulletin (overcast, rain, haze, storm, cold snap) started or ended around the drop, it is weather. An earthquake bulletin explains low throughput for many nights.
5. A comparison across a night boundary is weak evidence: nights differ by up to 40% from weather alone, so only a drop to about half or less with nothing announced points to a fault.
6. band_fail with band_pass 0 right at the drop means the sky itself got worse: weather.

Procedure: find the level before the drop and the level now; compute now/before; check whether the lower level has repeated; check bulletins; then give the probability. Be decisive: use values near 0.1 or 0.9 unless the evidence is genuinely mixed."""
def example(n):
    r = n["ratio"]
    if n["label"]: why, p = "steady near 0.82, one intermediate reading 0.67, then 0.43 and 0.41 repeating: a step to about half that holds, nothing announced.", 0.9
    elif any(k == "earthquake" for h in n["hist"] for k, _ in h["notices"]): why, p = "an earthquake bulletin is active and the last reading is below a quarter of the earlier level: closure-like, not a fault.", 0.05
    elif n["cross"]: why, p = "the only comparison is across a night boundary and the drop is small (about 20-25%): ordinary night-to-night weather.", 0.1
    else: why, p = "the readings slide a few percent at a time with no flat lower plateau, total drop about 20%: weather.", 0.1
    return base.raw(n) + f"\nReasoning: {why}\nPROBABILITY: {p}"
V2 = GUIDE
V3 = GUIDE + "\n\nWorked examples:\n\n" + "\n\n".join(example(n) for n in shots)
def features(n):
    vals = [h["rho"] for h in n["hist"] if h["rho"] is not None]
    now = vals[-1]; before = statistics.median(vals[:-1][:max(1, len(vals) - 3)]) if len(vals) > 1 else now
    steps = [b / a for a, b in zip(vals, vals[1:])]
    big = min(steps) if steps else 1.0
    tail = vals[-3:]; flat = (max(tail) - min(tail)) / max(tail) < 0.06 if len(tail) == 3 else False
    return (f"\nComputed by code: level before the drop {before:.2f}; level now {now:.2f}; now/before = {now / before:.2f}; "
            f"largest single-step ratio = {big:.2f}; last three readings {'are flat (a plateau)' if flat else 'are not flat'}; "
            f"comparison {'crosses a night boundary' if n['cross'] else 'is within one night'}.")
VARIANTS = {"v1": (V1, lambda n: base.raw(n)), "v2": (V2, lambda n: base.raw(n)), "v3": (V3, lambda n: base.raw(n)), "v4": (V3, lambda n: base.raw(n) + features(n))}
TAIL = "\n\nAnswer with only one line: PROBABILITY: <number between 0 and 1>"
def chat(system, user):
    body = {"model": "kimi-for-coding", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + TAIL}], "max_tokens": 8000, "reasoning_effort": "low"}
    t = time.perf_counter(); msg = None; raw_out = ""
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "-m", "200", URL, "-H", "@" + hdr.name, "-d", json.dumps(body)], capture_output=True, text=True); raw_out = r.stdout
        try: msg = json.loads(r.stdout)["choices"][0]["message"]; break
        except Exception: time.sleep(3 * (attempt + 1))  # noqa: BLE001
    if msg is None: return None, time.perf_counter() - t, raw_out[:160]
    m = re.findall(r"PROBABILITY:\s*([01](?:\.\d+)?)", msg.get("content") or "")
    return (float(m[-1]) if m else None), time.perf_counter() - t, ""
res = {v: [None] * len(sample) for v in WHICH}; lat = {v: [] for v in WHICH}; errs = []
def work(job):
    v, i = job; p, dt, err = chat(VARIANTS[v][0], VARIANTS[v][1](sample[i])); res[v][i] = p; lat[v].append(dt)
    if err: errs.append(err)
with ThreadPoolExecutor(6) as pool: list(pool.map(work, [(v, i) for v in WHICH for i in range(len(sample))]))
os.unlink(hdr.name)
def auc(vals):
    pos = [v for v, n in zip(vals, sample) if v is not None and n["label"]]; neg = [v for v, n in zip(vals, sample) if v is not None and not n["label"]]
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)) if pos and neg else float("nan")
np_, nn = sum(n["label"] for n in sample), sum(not n["label"] for n in sample)
print(f"[{STAGE}] {len(sample)} cases: {np_} faults, {nn} non-faults; kimi-for-coding, thinking effort low")
for v in WHICH:
    x = res[v]; tp = sum(1 for a, n in zip(x, sample) if a is not None and n["label"] and a >= .5); fp = sum(1 for a, n in zip(x, sample) if a is not None and not n["label"] and a >= .5)
    print(f"{v}: AUC {auc(x):.2f}  caught {tp}/{np_}  false alarms {fp}/{nn}  unparsed {sum(a is None for a in x)}  latency median {statistics.median(lat[v]):.1f}s max {max(lat[v]):.0f}s")
x = [n["r_agent_after"] for n in sample]
print(f"baseline flag: AUC {auc(x):.2f}  caught {sum(1 for a, n in zip(x, sample) if n['label'] and a >= .5)}/{np_}  false alarms {sum(1 for a, n in zip(x, sample) if not n['label'] and a >= .5)}/{nn}")
if errs: print("errors:", len(errs), errs[0])
(S / f"kimi_prompt_{STAGE}.json").write_text(json.dumps(res))
