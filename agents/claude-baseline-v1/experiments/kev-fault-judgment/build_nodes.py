"""Turn estimator traces + self-generated truth into labelled 'throughput dropped' decision nodes."""
import ast, csv, json, math, re, statistics, sys
from datetime import datetime, timezone
from pathlib import Path
R = Path(__file__).resolve().parents[2]
P = R.parents[1]
S = Path(sys.argv[1])
SETS = {'formal-': P / 'evaluation_assets/formal-v4-self-generated-v2/cards', 'holdout': R / 'eval_sets/holdout-reference-rates-s1/cards'}
def utc(t): return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
LINE = re.compile(r"^sky (\S+) T=(\d+) (\w+) match=(\d+) miss=(\d+) unk=(\d+) zero=(\d+) nrat=(\d+) rho=(\S+) W=(\S+) e=(\S+) low=(\d+) .*notices=(.*)$")
REPORT = re.compile(r"^report at (\S+): correct=(\w+)")
nodes = []
for trace in sorted((S / 'trace').glob('*.trace')):
    tag = trace.stem; prefix, card = tag.rsplit('-', 1)
    card_dir = SETS[prefix[:7]] / card
    faults = [(utc(e['actual_start_utc']), utc(e['actual_end_utc']), float(e['instrument_efficiency_multiplier']))
              for e in csv.DictReader((card_dir / 'truth/v4_events.csv').open()) if e['event_type'] == 'instrument_fault']
    rows, reports = [], []
    for line in trace.read_text().splitlines():
        m = LINE.match(line)
        if m:
            rho = float(m.group(9))
            rows.append(dict(t=utc(m.group(1)), ts=m.group(1), T=int(m.group(2)), prog=m.group(3), match=int(m.group(4)), miss=int(m.group(5)),
                             unk=int(m.group(6)), zero=int(m.group(7)), nrat=int(m.group(8)), rho=None if math.isnan(rho) else rho,
                             low=int(m.group(12)), notices=ast.literal_eval(m.group(13))))
            continue
        m = REPORT.match(line)
        if m and m.group(2) == 'True':
            reports.append(utc(m.group(1)))
    def fault_at(a, b):
        for start, end, mult in faults:
            repair = next((r for r in reports if start <= r < end), end)
            lo, hi = max(a, start), min(b, repair)
            if hi - lo >= 0.5 * (b - a):
                return mult, start
        return None
    quake_seen = -1e18
    for i, x in enumerate(rows):
        if any(k == 'earthquake' for k, _ in x['notices']): quake_seen = x['t']
        if x['rho'] is None or x['nrat'] < 1: continue
        same = [y for y in rows[max(0, i - 14):i] if y['rho'] is not None and 1200 <= x['t'] - y['t'] <= 9000]
        cross = False
        if len(same) < 2:
            prev_night = [y for y in rows[max(0, i - 40):i] if y['rho'] is not None and 6 * 3600 <= x['t'] - y['t'] <= 30 * 3600]
            if len(prev_night) < 3 or any(y['rho'] is not None and x['t'] - y['t'] < 6 * 3600 for y in rows[max(0, i - 6):i]):
                continue
            same, cross = prev_night[-3:], True
        ref = statistics.median(y['rho'] for y in same)
        ratio = x['rho'] / ref
        if ratio >= 0.80: continue
        hist = [y for y in rows[max(0, i - 8):i + 1]]
        prev = next((y for y in reversed(rows[max(0, i - 6):i]) if y['rho'] is not None and x['t'] - y['t'] < 5400), None)
        prev_ratio = prev['rho'] / ref if prev else None
        before_band = next((y['prog'] for y in reversed(same) if y['match'] > 0 and y['miss'] == 0), None)
        label = fault_at(x['t'], x['t'] + x['T'])
        nodes.append(dict(tag=tag, ts=x['ts'], ratio=ratio, prev_ratio=prev_ratio, cross=cross, prog=x['prog'], match=x['match'], miss=x['miss'],
                          zero=x['zero'], n=x['match'] + x['miss'] + x['unk'] + x['zero'], notices=x['notices'], before_band=before_band,
                          notice_changed=any(sorted(y['notices']) != sorted(x['notices']) for y in rows[max(0, i - 4):i] if x['t'] - y['t'] < 5400),
                          quake_recent=(x['t'] - quake_seen) < 10 * 86400, agent_low=x['low'],
                          label=label is not None, fault_mult=label[0] if label else None, since_onset=(x['t'] - label[1]) if label else None,
                          hist=[dict(ts=y['ts'][11:16], rho=y['rho'], prog=y['prog'], match=y['match'], miss=y['miss'], zero=y['zero'], notices=y['notices']) for y in hist]))
    print(tag, 'exposures', len(rows), 'faults', len(faults), 'reports', len(reports),
          'nodes', sum(1 for n in nodes if n['tag'] == tag), 'positive', sum(1 for n in nodes if n['tag'] == tag and n['label']))
(S / 'nodes.json').write_text(json.dumps(nodes))
print('total nodes', len(nodes), 'positive', sum(n['label'] for n in nodes))
