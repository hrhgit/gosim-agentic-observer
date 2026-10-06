"""Ask the local Kev about every 'throughput dropped' node and compare with simple rules."""
import json, statistics, sys, time, urllib.request
from pathlib import Path
S = Path(sys.argv[1]); URL = "http://127.0.0.1:8019/v1/systemone"
nodes = json.loads((S / 'nodes.json').read_text())
RULES = ("Background: a hidden instrument fault cuts the throughput suddenly, from one exposure to the next, to between 30% and 70% of normal. "
         "It leaves the sky band unchanged, is never announced in a bulletin, and stays until it is reported. "
         "Weather changes the throughput gradually or together with a bulletin, and usually lowers the sky band as well. "
         "A site closure or terrain removes almost all signal. After an earthquake the throughput is low for many nights. ")
QUESTION = {"type": "noul", "instructions": RULES + "Question: is a hidden instrument fault the cause of the low throughput now?"}
PLAIN = {"type": "noul", "instructions": "Is a hidden instrument fault (not weather, not a site closure) the cause of the low throughput now?"}
NAMES = {"overcast": "overcast", "rain": "rain", "haze": "haze", "cold_snap": "cold snap", "storm": "storm", "rocket_launch": "rocket launch", "earthquake": "earthquake", "terrain_obstruction": "terrain"}
def notice_text(ns):
    ns = [(k, d) for k, d in ns if k != 'terrain_obstruction']
    return "none" if not ns else ", ".join(f"{NAMES.get(k, k)} ({'whole sky' if d == 'ALL' else 'towards ' + d})" for k, d in ns)
def digest(n):
    pct = int(round(n['ratio'] * 20) * 5)
    size = "almost nothing is left" if n['ratio'] < 0.25 else "about half or less is left" if n['ratio'] < 0.6 else "moderately lower"
    if n['cross']: speed = "The comparison is with the end of the previous night."
    elif n['prev_ratio'] is None or n['prev_ratio'] >= 0.9: speed = "The fall was sudden: the previous exposure was still normal."
    elif abs(n['prev_ratio'] - n['ratio']) < 0.12 * n['prev_ratio']: speed = "The previous exposure was already at this low level; it is stable."
    else: speed = "The throughput has been sliding over several exposures."
    if n['match'] and not n['miss']:
        band = f"Sky band check passed: the sky still qualifies as {n['prog']}" + (", the same band as before the fall." if n['before_band'] == n['prog'] else ".")
    elif n['miss'] and not n['match']: band = f"Sky band check failed: the sky no longer qualifies as {n['prog']}."
    elif n['miss']: band = "Sky band check gave mixed results."
    else: band = "No sky band information in this exposure."
    lines = [f"Throughput is at {pct}% of the reference level ({size}).", speed, band,
             f"{n['zero']} of {n['n']} targets returned no signal at all.",
             f"Active weather bulletins: {notice_text(n['notices'])}.",
             "A bulletin started or ended within the last hour." if n['notice_changed'] else "No bulletin started or ended within the last hour.",
             "An earthquake was announced within the last ten nights." if n['quake_recent'] else "No earthquake recently."]
    return "\n".join(lines)
def raw(n):
    rows = [f"{h['ts']} program {h['prog']} throughput {('%.2f' % h['rho']) if h['rho'] is not None else 'n/a'} band_pass {h['match']} band_fail {h['miss']} no_signal {h['zero']} bulletins {notice_text(h['notices'])}" for h in n['hist']]
    return "Recent exposures, oldest first (the last line is now):\n" + "\n".join(rows) + ("\nAn earthquake was announced within the last ten nights." if n['quake_recent'] else "")
def ask(state, q):
    req = urllib.request.Request(URL, data=json.dumps({"model": "kev-latest", "state": state, "questions": {"fault": q}}).encode(), headers={"content-type": "application/json"})
    t = time.perf_counter()
    with urllib.request.urlopen(req, timeout=60) as r: body = json.load(r)
    return body["answers"]["fault"]["noul"], (time.perf_counter() - t) * 1000
lat = {"digest": [], "raw": [], "digest_plain": []}
for n in nodes:
    n['k_digest'], t = ask(digest(n), QUESTION); lat["digest"].append(t)
    n['k_raw'], t = ask(raw(n), QUESTION); lat["raw"].append(t)
    n['k_digest_plain'], t = ask(digest(n), PLAIN); lat["digest_plain"].append(t)
    sudden = (not n['cross']) and (n['prev_ratio'] is None or n['prev_ratio'] >= 0.9 or abs(n['prev_ratio'] - n['ratio']) < 0.12 * n['prev_ratio'])
    band_ok = not (n['miss'] and not n['match'])
    n['r_hand'] = 1.0 if (0.25 <= n['ratio'] < 0.8 and band_ok and not n['notice_changed'] and n['zero'] <= 0.3 * n['n'] and (sudden or n['cross'])) else 0.0
    n['r_drop'] = 1.0 - n['ratio']
    n['r_agent'] = 1.0 if n['agent_low'] > 0 else 0.0
(S / 'nodes_scored.json').write_text(json.dumps(nodes))
def auc(key):
    pos = [n[key] for n in nodes if n['label']]; neg = [n[key] for n in nodes if not n['label']]
    if not pos or not neg: return float('nan')
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))
def at(key, thr):
    tp = sum(1 for n in nodes if n['label'] and n[key] >= thr); fp = sum(1 for n in nodes if not n['label'] and n[key] >= thr)
    fn = sum(1 for n in nodes if n['label'] and n[key] < thr)
    return tp, fp, fn
P = sum(n['label'] for n in nodes); N = len(nodes) - P
print(f"nodes {len(nodes)}: fault {P}, not fault {N}")
for key, name in [('k_digest', 'Kev, digested state + rules in question'), ('k_digest_plain', 'Kev, digested state, plain question'), ('k_raw', 'Kev, raw numeric history + rules'),
                  ('r_hand', 'hand rule on the same digest'), ('r_drop', 'size of the drop only'), ('r_agent', "baseline agent's suspicion flag")]:
    tp, fp, fn = at(key, 0.5)
    print(f"{name:42s} AUC {auc(key):.3f}   at 0.5: caught {tp}/{P}, false alarms {fp}/{N}")
for key in ('k_digest', 'k_raw'):
    print(key, 'calibration (predicted band: n, mean predicted, actual fault rate):')
    for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]:
        b = [n for n in nodes if lo <= n[key] < hi]
        if b: print(f"   {lo:.1f}-{hi:.1f}: n={len(b):4d} predicted {statistics.mean(n[key] for n in b):.2f} actual {statistics.mean(n['label'] for n in b):.2f}")
for k, v in lat.items():
    q = statistics.quantiles(v, n=10); print(f"latency {k}: median {statistics.median(v):.0f} ms, p90 {q[8]:.0f} ms, max {max(v):.0f} ms over {len(v)} calls")
