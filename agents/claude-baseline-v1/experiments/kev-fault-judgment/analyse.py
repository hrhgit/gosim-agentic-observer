import json, re, statistics, sys, math
from pathlib import Path
S = Path(sys.argv[1])
nodes = json.loads((S / 'nodes_scored.json').read_text())
# the agent's flag one exposure later (its counter is printed before the current exposure is counted)
LINE = re.compile(r"^sky (\S+) T=\d+ .* low=(\d+) ")
after = {}
for trace in (S / 'trace').glob('*.trace'):
    rows = [(m.group(1), int(m.group(2))) for m in map(LINE.match, trace.read_text().splitlines()) if m]
    for (ts, _), (_, low_next) in zip(rows, rows[1:]):
        after[(trace.stem, ts)] = low_next
for n in nodes:
    n['r_agent_after'] = 1.0 if after.get((n['tag'], n['ts']), 0) > 0 else 0.0
def auc(key, subset):
    pos = [n[key] for n in subset if n['label']]; neg = [n[key] for n in subset if not n['label']]
    if not pos or not neg: return float('nan')
    return sum((p > q) + 0.5 * (p == q) for p in pos for q in neg) / (len(pos) * len(neg))
P = [n for n in nodes if n['label']]; N = [n for n in nodes if not n['label']]
tp = sum(n['r_agent_after'] for n in P); fp = sum(n['r_agent_after'] for n in N)
print(f"agent flag after counting this exposure: caught {tp:.0f}/{len(P)}, false alarms {fp:.0f}/{len(N)}, AUC {auc('r_agent_after', nodes):.3f}")
for key in ('k_digest', 'k_digest_plain', 'k_raw'):
    neg_sorted = sorted((n[key] for n in N), reverse=True)
    for allowed in (int(fp), 33, 66):
        thr = neg_sorted[allowed]
        print(f"  {key}: allowing {allowed} false alarms ({allowed/len(N):.0%}) catches {sum(1 for n in P if n[key] > thr)}/{len(P)}")
flagged = [n for n in nodes if n['r_agent_after']]; unflagged = [n for n in nodes if not n['r_agent_after']]
for key in ('k_digest', 'k_digest_plain'):
    print(f"{key}: AUC inside agent-flagged nodes ({sum(n['label'] for n in flagged)} faults / {len(flagged)}) = {auc(key, flagged):.3f}; "
          f"inside unflagged ({sum(n['label'] for n in unflagged)} faults / {len(unflagged)}) = {auc(key, unflagged):.3f}")
print("spread of Kev answers (digest+rules): min %.2f p10 %.2f median %.2f p90 %.2f max %.2f" % (
    min(n['k_digest'] for n in nodes), statistics.quantiles([n['k_digest'] for n in nodes], n=10)[0], statistics.median(n['k_digest'] for n in nodes),
    statistics.quantiles([n['k_digest'] for n in nodes], n=10)[8], max(n['k_digest'] for n in nodes)))
# per fault: first node after onset
first = {}
for n in sorted(P, key=lambda n: n['since_onset']):
    first.setdefault((n['tag'], round(n['fault_mult'], 3)), n)
print(f"faults with at least one node: {len(first)} of 18")
negs = sorted(n['k_digest'] for n in N)
for key, n in sorted(first.items()):
    rank = sum(1 for v in negs if v < n['k_digest']) / len(negs)
    print(f"  {key[0]} x{key[1]:.2f}: first reading {n['since_onset']/60:.0f} min after onset, ratio {n['ratio']:.2f}, Kev {n['k_digest']:.2f} (above {rank:.0%} of non-faults), agent flag then {int(n['r_agent_after'])}")
