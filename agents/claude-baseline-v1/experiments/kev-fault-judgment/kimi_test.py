"""A small sample of the fault-or-weather cases sent to Kimi models at different thinking efforts (same prompt as the DeepSeek run)."""
import json, os, random, re, statistics, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
S = Path(sys.argv[1]); ENV = Path(sys.argv[2])
sys.path.insert(0, str(S)); import eval_kev_lib as base
key = next(l.split("=", 1)[1].strip().strip('"') for l in ENV.read_text().splitlines() if l.startswith("KIMI_API_KEY="))
hdr = tempfile.NamedTemporaryFile("w", suffix=".hdr", delete=False); os.chmod(hdr.name, 0o600)
hdr.write("authorization: Bearer " + key + "\ncontent-type: application/json\n"); hdr.close()
URL = "https://api.kimi.ai/coding/v1/chat/completions"
SYSTEM = ("You monitor a survey telescope. " + base.RULES + "Decide whether a hidden instrument fault is the cause of the low throughput now.")
TAIL = "\n\nAnswer with only one line: PROBABILITY: <number between 0 and 1>"
def chat(model, effort, user):
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user + TAIL}], "max_tokens": 16000, "reasoning_effort": effort}
    t = time.perf_counter(); out = {}
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "-m", "300", URL, "-H", "@" + hdr.name, "-d", json.dumps(body)], capture_output=True, text=True)
        try:
            out = json.loads(r.stdout); msg = out["choices"][0]["message"]; break
        except Exception:  # noqa: BLE001
            msg = None; time.sleep(3 * (attempt + 1))
    dt = time.perf_counter() - t
    if msg is None: return None, dt, 0, (r.stdout or r.stderr)[:200]
    m = re.findall(r"PROBABILITY:\s*([01](?:\.\d+)?)", msg.get("content") or "")
    u = out.get("usage", {})
    return (float(m[-1]) if m else None), dt, (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or u.get("completion_tokens", 0), ""
nodes = json.loads((S / "k4" / "nodes_scored_kev4b.json").read_text())
rng = random.Random(3)
sample = rng.sample([n for n in nodes if n["label"]], 15) + rng.sample([n for n in nodes if not n["label"]], 15)
if len(sys.argv) > 3:      # probe
    print(chat(sys.argv[3], sys.argv[4], base.raw(sample[0]))); os.unlink(hdr.name); sys.exit()
configs = [(m, e) for m in ("k3-256k", "kimi-for-coding") for e in ("low", "high", "max")]
results = {c: [None] * len(sample) for c in configs}; meta = {c: [] for c in configs}; errors = []
def work(job):
    c, i = job
    p, dt, tokens, err = chat(c[0], c[1], base.raw(sample[i]))
    results[c][i] = p; meta[c].append((dt, tokens))
    if err: errors.append(err)
with ThreadPoolExecutor(8) as pool: list(pool.map(work, [(c, i) for c in configs for i in range(len(sample))]))
os.unlink(hdr.name)
def auc(vals):
    pos = [v for v, n in zip(vals, sample) if v is not None and n["label"]]; neg = [v for v, n in zip(vals, sample) if v is not None and not n["label"]]
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)) if pos and neg else float("nan")
print("30 cases: 15 faults, 15 non-faults; raw-number prompt, same wording as the DeepSeek run")
for c in configs:
    v = results[c]
    tp = sum(1 for x, n in zip(v, sample) if x is not None and n["label"] and x >= 0.5); fp = sum(1 for x, n in zip(v, sample) if x is not None and not n["label"] and x >= 0.5)
    dts = [d for d, _ in meta[c]]; toks = [k for _, k in meta[c]]
    print(f"{c[0]:16s} effort {c[1]:4s} AUC {auc(v):.2f}  caught {tp}/15  false alarms {fp}/15  unparsed {sum(x is None for x in v)}  latency median {statistics.median(dts):.1f}s max {max(dts):.0f}s  thinking tokens median {statistics.median(toks):.0f}")
for key2, name in (("r_agent_after", "baseline flag"), ):
    v = [n[key2] for n in sample]; print(f"{name:16s}             AUC {auc(v):.2f}  caught {sum(1 for x, n in zip(v, sample) if n['label'] and x >= .5)}/15  false alarms {sum(1 for x, n in zip(v, sample) if not n['label'] and x >= .5)}/15")
ds = json.loads((S / "nodes_scored_llm_deepseek-flash_direct.json").read_text()); look = {(n["tag"], n["ts"]): n for n in ds}
v = [look[(n["tag"], n["ts"])]["l_raw"] for n in sample]
print(f"deepseek-flash (no thinking)     AUC {auc(v):.2f}  caught {sum(1 for x, n in zip(v, sample) if n['label'] and x >= .5)}/15  false alarms {sum(1 for x, n in zip(v, sample) if not n['label'] and x >= .5)}/15")
if errors: print("errors:", len(errors), errors[0])
(S / "kimi_results.json").write_text(json.dumps({f"{m}|{e}": results[(m, e)] for m, e in configs}))
