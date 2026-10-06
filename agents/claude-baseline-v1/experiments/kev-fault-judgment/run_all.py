"""All fault-judgment variants in one pass against whatever model the local Kev server is serving.

    python run_all.py <folder with nodes.json, trace/, eval_kev_lib.py> <label> [workers]
"""
import json, math, re, statistics, sys, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
S = Path(sys.argv[1]); LABEL = sys.argv[2]; WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 4
sys.path.insert(0, str(S)); import eval_kev_lib as base
def utc(t): return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
LINE = re.compile(r"^sky (\S+) T=(\d+) (\w+) match=(\d+) miss=(\d+) unk=\d+ zero=\d+ nrat=(\d+) rho=(\S+) W=(\S+) e=(\S+) low=(\d+) lower=(\S+) upper=(\S+) ")
rows = {}
for trace in (S / 'trace').glob('*.trace'):
    out = []
    for line in trace.read_text().splitlines():
        m = LINE.match(line)
        if m:
            rho = float(m.group(7))
            out.append(dict(ts=m.group(1), t=utc(m.group(1)), rho=None if math.isnan(rho) else rho, low=int(m.group(10)),
                            lower=None if m.group(11) == 'None' else float(m.group(11)), upper=None if m.group(12) == 'None' else float(m.group(12))))
    rows[trace.stem] = out
index = {(tag, r['ts']): i for tag, out in rows.items() for i, r in enumerate(out)}
nodes = json.loads((S / 'nodes.json').read_text())
import os, random
if os.environ.get("SUBSAMPLE_NEG"):
    rng = random.Random(7)
    neg = [n for n in nodes if not n['label']]
    nodes = [n for n in nodes if n['label']] + rng.sample(neg, int(os.environ["SUBSAMPLE_NEG"]))
def extra(n):
    out = rows[n['tag']]; i = index[(n['tag'], n['ts'])]; x = out[i]
    ref = [y['rho'] for y in out[max(0, i - 14):i] if y['rho'] is not None and 1200 <= x['t'] - y['t'] <= 9000]
    spread = (max(ref) - min(ref)) / statistics.median(ref) if len(ref) >= 2 else None
    lows = 0
    for y in reversed(out[max(0, i - 8):i]):
        if y['rho'] is not None and x['t'] - y['t'] < 9000 and ref and y['rho'] < 0.8 * statistics.median(ref): lows += 1
        else: break
    ceiling = min(1.0, x['rho'] / (0.95 * x['lower'])) if x['lower'] else None
    weather_fits = x['upper'] is not None and x['rho'] / 0.95 <= x['upper'] * 1.08
    n['r_agent_after'] = 1.0 if (i + 1 < len(out) and out[i + 1]['low'] > 0) else 0.0
    lines = []
    if ceiling is not None and ceiling < 0.80:
        lines.append(f"Sky band proof: the sky is demonstrably clear enough that a healthy instrument would deliver much more. The instrument is delivering at most {int(round(ceiling * 20) * 5)}% of normal.")
    elif ceiling is not None: lines.append("Sky band proof: the sky itself is dimmer; a healthy instrument would deliver about what is observed.")
    elif weather_fits: lines.append("Sky band proof: the sky is no better than the throughput suggests; weather alone explains the level.")
    else: lines.append("Sky band proof: none available in this exposure.")
    if spread is not None: lines.append("Before the fall the throughput was steady." if spread < 0.12 else "Before the fall the throughput was already fluctuating.")
    lines.append(f"Consecutive earlier exposures already at the low level: {lows}.")
    return "\n".join(lines)
RICH_RULES = base.RULES + ("The strongest evidence for a fault is a sky band proof that the instrument delivers far less than the sky allows. "
                           "If the sky itself is dimmer, or the throughput was already fluctuating, it is weather. ")
QR = {"type": "noul", "instructions": RICH_RULES + "Question: is a hidden instrument fault the cause of the low throughput now?"}
jobs = []
for i, n in enumerate(nodes):
    more = extra(n); d = base.digest(n)
    if os.environ.get('SKIP_PLAIN'): n['k_digest_plain'] = 0.0
    else: jobs.append((i, 'k_digest_plain', d, base.PLAIN))
    jobs += [(i, 'k_digest', d, base.QUESTION), (i, 'k_raw', base.raw(n), base.QUESTION),
             (i, 'k_rich', d + "\n" + more, QR),
             (i, 'k_stack', d + "\n" + more + "\nThe rule-based monitor currently " + ("suspects a fault." if n['r_agent_after'] else "does not suspect a fault."), QR)]
started = time.perf_counter(); done = [0]
def work(job):
    i, key, state, q = job
    value, ms = base.ask(state, q)
    nodes[i][key] = value; done[0] += 1
    if done[0] % 500 == 0: print(f"  {done[0]}/{len(jobs)} after {time.perf_counter() - started:.0f} s", file=sys.stderr, flush=True)
with ThreadPoolExecutor(WORKERS) as pool: list(pool.map(work, jobs))
elapsed = time.perf_counter() - started
(S / f'nodes_scored_{LABEL}.json').write_text(json.dumps(nodes))
P = [n for n in nodes if n['label']]; N = [n for n in nodes if not n['label']]
def auc(key, subset=None):
    subset = subset or nodes
    pos = [n[key] for n in subset if n['label']]; neg = [n[key] for n in subset if not n['label']]
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')
def caught(key, allowed=None):
    allowed = allowed if allowed is not None else round(58 / 655 * len(N))
    thr = sorted((n[key] for n in N), reverse=True)[allowed]
    return sum(1 for n in P if n[key] > thr)
print(f"[{LABEL}] {len(jobs)} calls in {elapsed:.0f} s with {WORKERS} at a time ({1000 * elapsed / len(jobs):.0f} ms per call of wall time)")
print(f"nodes {len(nodes)}: fault {len(P)}, not fault {len(N)}")
for key, name in [('k_digest', 'digest + rules'), ('k_digest_plain', 'digest, plain question'), ('k_raw', 'raw numbers + rules'), ('k_rich', 'richer digest + rules'),
                  ('k_stack', 'richer digest + monitor flag'), ('r_agent_after', "baseline's flag")]:
    vals = sorted(n[key] for n in nodes)
    tp = sum(1 for n in P if n[key] >= 0.5); fp = sum(1 for n in N if n[key] >= 0.5)
    print(f"{name:30s} AUC {auc(key):.3f}  catches {caught(key):2d}/{len(P)} at the same false-alarm rate (9%) | at 0.5: {tp}/{len(P)} caught, {fp}/{len(N)} false | answers p10 {vals[len(vals)//10]:.2f} median {vals[len(vals)//2]:.2f} p90 {vals[9*len(vals)//10]:.2f}")
flagged = [n for n in nodes if n['r_agent_after']]
for key in ('k_digest', 'k_rich'):
    print(f"inside the {len(flagged)} nodes the baseline flags ({sum(n['label'] for n in flagged)} real faults): {key} AUC {auc(key, flagged):.3f}")
for key in ('k_digest', 'k_rich'):
    print(key, 'calibration:', "; ".join(f"{lo:.1f}-{hi:.1f}: n={len(b)} actual {statistics.mean(x['label'] for x in b):.2f}"
          for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)] for b in [[x for x in nodes if lo <= x[key] < hi]] if b))
