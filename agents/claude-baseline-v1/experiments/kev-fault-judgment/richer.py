"""Second pass: give Kev more of what the estimator knows and see whether its judgment improves."""
import json, math, re, statistics, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
S = Path(sys.argv[1]); URL = "http://127.0.0.1:8019/v1/systemone"
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
nodes = json.loads((S / 'nodes_scored.json').read_text())
def extra(n):
    out = rows[n['tag']]; i = index[(n['tag'], n['ts'])]; x = out[i]
    ref = [y['rho'] for y in out[max(0, i - 14):i] if y['rho'] is not None and 1200 <= x['t'] - y['t'] <= 9000]
    spread = (max(ref) - min(ref)) / statistics.median(ref) if len(ref) >= 2 else None
    lows = 0
    for y in reversed(out[max(0, i - 8):i]):
        if y['rho'] is not None and x['t'] - y['t'] < 9000 and ref and y['rho'] < 0.8 * statistics.median(ref): lows += 1
        else: break
    ceiling = min(1.0, x['rho'] / (0.95 * x['lower'])) if x['lower'] else None      # band proves W >= lower, so efficiency <= rho / lower
    weather_fits = x['upper'] is not None and x['rho'] / 0.95 <= x['upper'] * 1.08
    n['x_ceiling'], n['x_spread'], n['x_lows'], n['x_weather_fits'] = ceiling, spread, lows, weather_fits
    n['r_agent_after'] = 1.0 if (i + 1 < len(out) and out[i + 1]['low'] > 0) else 0.0
    lines = []
    if ceiling is not None and ceiling < 0.80:
        lines.append(f"Sky band proof: the sky is demonstrably clear enough that a healthy instrument would deliver much more. The instrument is delivering at most {int(round(ceiling * 20) * 5)}% of normal.")
    elif ceiling is not None:
        lines.append("Sky band proof: the sky itself is dimmer; a healthy instrument would deliver about what is observed.")
    elif weather_fits:
        lines.append("Sky band proof: the sky is no better than the throughput suggests; weather alone explains the level.")
    else:
        lines.append("Sky band proof: none available in this exposure.")
    if spread is not None:
        lines.append("Before the fall the throughput was steady." if spread < 0.12 else "Before the fall the throughput was already fluctuating.")
    lines.append(f"Consecutive earlier exposures already at the low level: {lows}.")
    return "\n".join(lines)
RULES = base.RULES + ("The strongest evidence for a fault is a sky band proof that the instrument delivers far less than the sky allows. "
                      "If the sky itself is dimmer, or the throughput was already fluctuating, it is weather. ")
Q = {"type": "noul", "instructions": RULES + "Question: is a hidden instrument fault the cause of the low throughput now?"}
lat = []
for n in nodes:
    more = extra(n)
    state = base.digest(n) + "\n" + more
    n['k_rich'], t = base.ask(state, Q); lat.append(t)
    n['k_stack'], t = base.ask(state + "\nThe rule-based monitor currently " + ("suspects a fault." if n['r_agent_after'] else "does not suspect a fault."), Q)
    n['r_ceiling'] = (1.0 - n['x_ceiling']) if n['x_ceiling'] is not None else 0.0
(S / 'nodes_scored2.json').write_text(json.dumps(nodes))
P = [n for n in nodes if n['label']]; N = [n for n in nodes if not n['label']]
def auc(key, subset=nodes):
    pos = [n[key] for n in subset if n['label']]; neg = [n[key] for n in subset if not n['label']]
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')
def caught(key, allowed=58):
    thr = sorted((n[key] for n in N), reverse=True)[allowed]
    return sum(1 for n in P if n[key] > thr)
print(f"nodes with a sky band proof of lost efficiency (<80%): faults {sum(1 for n in P if n['x_ceiling'] is not None and n['x_ceiling'] < 0.8)}/{len(P)}, "
      f"non-faults {sum(1 for n in N if n['x_ceiling'] is not None and n['x_ceiling'] < 0.8)}/{len(N)}")
for key, name in [('k_digest', 'Kev, first digest'), ('k_rich', 'Kev, richer digest'), ('k_stack', 'Kev, richer digest + monitor flag'),
                  ('r_ceiling', 'rule: band proof alone'), ('r_agent_after', "baseline's flag")]:
    print(f"{name:36s} AUC {auc(key):.3f}   catches {caught(key)}/{len(P)} when 58 false alarms are allowed")
vals = sorted(n['k_rich'] for n in nodes)
print("spread of richer answers: min %.2f p10 %.2f median %.2f p90 %.2f max %.2f" % (vals[0], vals[len(vals)//10], vals[len(vals)//2], vals[9*len(vals)//10], vals[-1]))
for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]:
    b = [n for n in nodes if lo <= n['k_rich'] < hi]
    if b: print(f"   answer {lo:.1f}-{hi:.1f}: n={len(b):4d} actual fault rate {statistics.mean(n['label'] for n in b):.2f}")
print(f"latency richer digest: median {statistics.median(lat):.0f} ms")
