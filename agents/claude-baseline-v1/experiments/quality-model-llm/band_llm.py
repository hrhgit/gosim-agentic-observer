"""Does an LLM declare the observing band better from raw public data, from the trained model's output, or from both?
Rows come from the 16 final test scenarios of the reliability experiment (self-generated weather). kimi-for-coding, low effort."""
import json, os, random, re, statistics, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import numpy as np
from research.observing_conditions import reliability_data as rd
OUT = Path("artifacts/observing_conditions-reliability"); STAGE = sys.argv[1]; WHICH = sys.argv[2].split(","); SAVE = Path(sys.argv[3])
data = rd.load(OUT, "test"); z = np.load(OUT / "test_predictions.npz")
y, hold, meta, scene = data["y"], data["hold"], data["meta"], data["scene"]
nn = z["gru64/fresh_hybrid_12/endpoint_raw"]; tree = z["tree64/previous"]
BANDS = ("DARK", "BRIGHT", "BACKUP"); MULT = np.array([1.20, 1.12, 1.06])
def ts(s): return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
ok = [i for i, m in enumerate(meta) if m["horizon"] == "15m" and m["history_count"] >= 3 and not m["suspected_fault"]]
rng = random.Random(21 if STAGE == "dev" else 22)
pick = []
for band, k in ((0, 16 if STAGE == "test" else 8), (1, 14 if STAGE == "test" else 7), (2, 10 if STAGE == "test" else 5)):
    pool = [i for i in ok if int(y[i, 3]) == band]
    if STAGE == "test":      # keep the tuning rows out of the check
        dev_rng = random.Random(21); used = set()
        for b2, k2 in ((0, 8), (1, 7), (2, 5)):
            used |= set(dev_rng.sample([i for i in ok if int(y[i, 3]) == b2], k2))
        pool = [i for i in pool if i not in used]
    pick += rng.sample(pool, k)
_cache = {}
def scenario(name):
    if name not in _cache:
        d = OUT / "scenarios" / name
        decisions = {x["now_utc"]: x for x in json.loads((d / "decisions.json").read_text())}
        bulletins = [json.loads(l) for l in (d / "bundle/generated/v4_bulletins.jsonl").read_text().splitlines()]
        forecasts = [json.loads(l) for l in (d / "bundle/generated/v4_forecasts.jsonl").read_text().splitlines()]
        _cache[name] = (decisions, bulletins, forecasts)
    return _cache[name]
def notices(ns): return ", ".join(f"{n['event_kind']} ({n.get('direction', 'ALL')})" for n in ns if n["event_kind"] != "terrain_obstruction") or "none"
def raw_block(i):
    m = meta[i]; decisions, bulletins, forecasts = scenario(str(scene[i])); d = decisions[m["now_utc"]]; now = ts(m["now_utc"])
    past = [b for b in bulletins if ts(b["issued_at_utc"]) <= now]
    recent = past[-5:]
    fc = [f for f in forecasts if ts(f["issued_at_utc"]) <= now]
    st = d["baseline_state"]; W = st["weather"]
    geo = float(hold[i, 0]) / W if W > 1e-6 else float("nan")
    lines = [f"Now {m['now_utc'][:16]}. The exposure would start in 15 minutes and last {m['duration_seconds'] // 60} min.",
             "Recent exposures (minutes ago, measured sky level = throughput / instrument efficiency, band checks passed/failed, no-signal targets):"]
    for h in d["feedback_history"]:
        c = h["counts"]; lvl = h["throughput_mid"] / max(h["estimated_efficiency"], 1e-6) if h.get("throughput_mid") is not None else None
        lines.append(f"  {int((now - ts(h['end_utc'])) / 60):4d} min ago: sky level {('%.2f' % lvl) if lvl is not None else 'n/a'}, band passed {c['matched']}, failed {c['mismatch']}, no signal {c['zero']}")
    lines.append("Bulletins in the last five slots (oldest first): " + " | ".join(notices(b["notices"]) for b in recent))
    lines.append("Latest forecast notices: " + (notices(fc[-1]["notices"]) if fc else "none") + (f" for nights {sorted({n2 for n in fc[-1]['notices'] for n2 in n.get('nights', [])})}" if fc and fc[-1]["notices"] else ""))
    lines.append(f"Geometry factor of the candidate position during the exposure (airmass and moonlight, from the public formula): {geo:.2f}.")
    lines.append("Band rule: band level = sky level x geometry factor. DARK if band level >= 0.65, BRIGHT if >= 0.40, otherwise BACKUP.")
    return "\n".join(lines)
def nn_block(i):
    p = nn[i]; m = meta[i]
    return (f"A trained forecasting model (it has seen bulletins, forecasts and the recent exposures) predicts for this exposure:\n"
            f"  probability DARK {p[9]:.2f}, BRIGHT {p[10]:.2f}, BACKUP {p[11]:.2f}\n"
            f"  quality: mean {p[0]:.2f}, 5%-95% range {p[1]:.2f} to {p[3]:.2f}\n"
            f"  usable fraction of the exposure: mean {p[4]:.2f}; probability of severe blockage {p[8]:.2f}\n"
            f"  exposures already completed tonight: {m.get('fresh_history_count', m['history_count'])}")
PAY = ("You declare the observing band for one telescope exposure. If the declared band equals the real one the exposure score is multiplied by "
       "1.20 (DARK), 1.12 (BRIGHT) or 1.06 (BACKUP); a wrong declaration gives 1.00. ")
SYS = {"raw": PAY + "Use the recent measurements and bulletins to judge the sky 15 minutes from now.",
       "nn": PAY + "Use the model's probabilities.",
       "both": PAY + "You get the raw recent data and a trained model's prediction.",
       "both_opt": PAY + ("You get the raw recent data and a trained model's prediction. How to combine them: "
                          "1) Start from the model's probabilities and pick the band with the highest expected gain = probability x (multiplier - 1), i.e. 0.20 x P(DARK), 0.12 x P(BRIGHT), 0.06 x P(BACKUP). "
                          "DARK therefore wins unless P(DARK) is below about 0.6 x P(BRIGHT). "
                          "2) The model is calibrated on average but reacts slowly to the last few minutes. Override it only on clear raw evidence: "
                          "if the newest readings (under 20 min old) put sky level x geometry factor more than 15% on the other side of a threshold than the model implies, trust the readings; "
                          "if a band check failed in the newest exposure, the band just dropped; if a whole-sky bulletin started or ended in the last slots, expect a change in that direction. "
                          "3) With fewer than 3 exposures tonight the model's BRIGHT/BACKUP probabilities are unreliable: lean on the raw readings.")}
USER = {"raw": lambda i: raw_block(i), "nn": lambda i: nn_block(i), "both": lambda i: raw_block(i) + "\n\n" + nn_block(i), "both_opt": lambda i: raw_block(i) + "\n\n" + nn_block(i)}
key = next(l.split("=", 1)[1].strip().strip('"') for l in Path("challenge/participant_agent/.env").read_text().splitlines() if l.startswith("KIMI_API_KEY="))
hdr = tempfile.NamedTemporaryFile("w", suffix=".hdr", delete=False); os.chmod(hdr.name, 0o600); hdr.write("authorization: Bearer " + key + "\ncontent-type: application/json\n"); hdr.close()
def chat(system, user):
    body = {"model": "kimi-for-coding", "messages": [{"role": "system", "content": system}, {"role": "user", "content": user + "\n\nAnswer with only one line: BAND: DARK, BAND: BRIGHT or BAND: BACKUP"}], "max_tokens": 8000, "reasoning_effort": "low"}
    t = time.perf_counter(); msg = None; raw = ""
    for attempt in range(3):
        r = subprocess.run(["curl", "-s", "-m", "200", "https://api.kimi.ai/coding/v1/chat/completions", "-H", "@" + hdr.name, "-d", json.dumps(body)], capture_output=True, text=True); raw = r.stdout
        try: msg = json.loads(r.stdout)["choices"][0]["message"]; break
        except Exception: time.sleep(3 * (attempt + 1))  # noqa: BLE001
    found = re.findall(r"BAND:\s*(DARK|BRIGHT|BACKUP)", (msg or {}).get("content") or "")
    return (BANDS.index(found[-1]) if found else None), time.perf_counter() - t, ("" if msg else raw[:160])
if len(sys.argv) > 4:
    print(SYS["both_opt"]); print(USER["both"](pick[0])); print("truth", BANDS[int(y[pick[0], 3])]); os.unlink(hdr.name); sys.exit()
res = {v: [None] * len(pick) for v in WHICH}; lat = {v: [] for v in WHICH}; errs = []
def work(job):
    v, k = job; a, dt, err = chat(SYS[v], USER[v](pick[k])); res[v][k] = a; lat[v].append(dt)
    if err: errs.append(err)
with ThreadPoolExecutor(6) as pool: list(pool.map(work, [(v, k) for v in WHICH for k in range(len(pick))]))
os.unlink(hdr.name)
truth = np.array([int(y[i, 3]) for i in pick])
def report(name, choice):
    choice = np.array([(-1 if c is None else c) for c in choice]); hit = choice == truth
    mult = np.where(hit, MULT[truth], 1.0).mean()
    per = "/".join(f"{int(hit[truth == b].sum())} of {int((truth == b).sum())}" for b in range(3))
    print(f"{name:34s} right {int(hit.sum()):2d}/{len(pick)}  mean multiplier {mult:.4f}  (DARK/BRIGHT/BACKUP cases right: {per})")
print(f"[{STAGE}] {len(pick)} exposures 15 min ahead: {[int((truth == b).sum()) for b in range(3)]} truly DARK/BRIGHT/BACKUP (rare bands deliberately over-sampled)")
report("always DARK", [0] * len(pick))
report("hold baseline estimate, most likely", hold[pick][:, 9:12].argmax(1))
report("model GRU, most likely", nn[pick][:, 9:12].argmax(1))
report("model GRU, best expected gain", (nn[pick][:, 9:12] * (MULT - 1)).argmax(1))
report("model tree, best expected gain", (tree[pick][:, 9:12] * (MULT - 1)).argmax(1))
for v in WHICH:
    report(f"kimi: {v}", res[v]); print(f"    unparsed {sum(a is None for a in res[v])}, latency median {statistics.median(lat[v]):.1f}s")
report("perfect", truth)
if errs: print("errors", len(errs), errs[0])
SAVE.write_text(json.dumps({"rows": pick, "truth": truth.tolist(), "answers": res}))
