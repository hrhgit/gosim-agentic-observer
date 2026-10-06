"""Ask the local Kev about every 'throughput dropped' node and compare with simple rules."""
import json, statistics, sys, time, urllib.request
from pathlib import Path
S = Path(sys.argv[1]); URL = "http://127.0.0.1:8019/v1/systemone"

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
