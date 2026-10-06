"""Experiment: something other than the planner's own score picks the rough sky region; the
planner then places the field inside it. BASE_CHOOSER = planner | random | llm."""
import json, os, random, re, sys, time, urllib.request

MODE = os.environ.get("BASE_CHOOSER", "planner")
WIDTH = 30.0
_rng = random.Random(11)
_cfg = None
stats = {"calls": 0, "overrides": 0, "errors": 0, "wait_s": 0.0, "single": 0}
_cache = {"n": -99, "done": {}}
EVERY = int(os.environ.get("BASE_CHOOSER_EVERY", "4"))
_sticky = {"left": 0, "region": None}
LOG = os.environ.get("BASE_CHOOSER_LOG")

SYSTEM = (
    "You steer a survey telescope. A planner proposes the best field in each sky region that is observable right now. "
    "The planner is greedy: its score only reflects what the next exposure earns, lightly adjusted for regions that leave the sky soon. "
    "Final score = sum over targets of the best single exposure each one got, so sky that sets for the season with unobserved targets is lost for good, "
    "while sky that stays visible for months can be done later. Each region line gives: planner score relative to the best (100 = planner's choice), "
    "exposure minutes, how complete the region already is, hours of visibility left tonight and in the whole season, and required/request targets in the field. "
    "Pick the region to observe next so that the season total is highest. Answer with the region label only, for example R3.")


def _config():
    global _cfg
    if _cfg is None:
        path = os.environ["BASE_LLM_ENV"]
        _cfg = dict(line.split("=", 1) for line in open(path).read().splitlines() if "=" in line and not line.lstrip().startswith("#"))
    return _cfg


def region_of(ra):
    return int(ra // WIDTH)


def completeness(sv):
    """Share of the ceiling (weight x 1.2) already banked per region; refreshed every 25 calls."""
    if stats["calls"] - _cache["n"] >= 25:
        cat = sv.cat
        got, top = {}, {}
        for i in range(len(cat.weight)):
            r = region_of(cat.ra[i])
            got[r] = got.get(r, 0.0) + sv.best[i]
            top[r] = top.get(r, 0.0) + 1.2 * cat.weight[i]
        _cache["done"] = {r: got[r] / top[r] for r in top}
        _cache["pool"] = {r: top[r] - got[r] for r in top}
        _cache["n"] = stats["calls"]
    return _cache["done"]


def choose(options, sv, t, night, lst):
    """options: list of dicts (region, score, minutes, alt, hours_tonight, season_hours, required, request), best score first."""
    stats["calls"] += 1
    if len(options) < 2:
        stats["single"] += 1
        return 0
    # one decision holds for the next EVERY - 1 exposures (about an hour), as long as that region is still on offer
    if _sticky["left"] > 0:
        _sticky["left"] -= 1
        for k, o in enumerate(options):
            if o["region"] == _sticky["region"]:
                if k != 0:
                    stats["overrides"] += 1
                return k
    stats["asked"] = stats.get("asked", 0) + 1
    if MODE == "random":
        pick = _rng.randrange(len(options))
    elif MODE == "llm":
        done = completeness(sv)
        top = options[0]["score"]
        nights_left = len(sv.nights) - night
        lines = [f"Night {night + 1} of {len(sv.nights)} ({nights_left} left). {(sv.nights[night][1] - t) / 3600.0:.1f} hours of this night remain."]
        for k, o in enumerate(options):
            lines.append(f"R{k + 1}: RA {int(o['region'] * WIDTH)}-{int((o['region'] + 1) * WIDTH)} deg | planner score {100 * o['score'] / top:.0f} | exposure {o['minutes']:.0f} min | "
                         f"region {100 * done.get(o['region'], 0):.0f}% complete | altitude {o['alt']:.0f} deg | visible {o['hours_tonight']:.1f} h more tonight, "
                         f"{o['season_hours']:.0f} h more this season | required targets in field {o['required']} | request targets {o['request']}")
        body = {"model": _config()["MODEL_NAME"].strip(), "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n".join(lines)}],
                "max_tokens": 6, "temperature": 0, "thinking": {"type": "disabled"}}
        pick = 0
        began = time.perf_counter()
        for attempt in range(3):
            try:
                req = urllib.request.Request(_config()["MODEL_BASE_URL"].strip().rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                             headers={"content-type": "application/json", "authorization": "Bearer " + _config()["MODEL_API_KEY_ENV"].strip()})
                with urllib.request.urlopen(req, timeout=20) as r:
                    text = json.load(r)["choices"][0]["message"]["content"] or ""
                digits = "".join(ch for ch in text if ch.isdigit())
                pick = int(digits) - 1 if digits else 0
                if not 0 <= pick < len(options):
                    pick = 0
                break
            except Exception:  # noqa: BLE001 - any failure falls back to the planner's choice
                stats["errors"] += 1
                time.sleep(1 + attempt)
        stats["wait_s"] += time.perf_counter() - began
        if stats["calls"] % 200 == 0:
            print(f"chooser: {stats}", file=sys.stderr, flush=True)
    else:
        pick = 0
    if pick != 0:
        stats["overrides"] += 1
    _sticky["region"] = options[pick]["region"]
    _sticky["left"] = EVERY - 1
    if LOG:
        completeness(sv)
        pool = _cache.get("pool", {})
        with open(LOG, "a") as handle:
            handle.write(json.dumps({"t": t, "night": night, "pick": pick, "options": [dict(o, pool=pool.get(o["region"], 0.0)) for o in options]}) + "\n")
    return pick


# ---- experiment 2: the model sets the exposure length ----------------------------------------
DMODE = os.environ.get("BASE_DURATION", "off")          # off | abs | rel
dstats = {"calls": 0, "changed": 0, "longer": 0, "shorter": 0, "errors": 0, "wait_s": 0.0}
D_SYSTEM = (
    "You set the exposure length for one field of a survey telescope with many fibres. All fibres share the exposure length. "
    "A target's score grows with exposure time until it saturates; only the single best exposure of each target counts, so a target left "
    "unsaturated now must later be re-observed with a full, longer exposure and the time spent now is then wasted. Exposing longer than "
    "the bright targets need wastes their fibres. Time is scarce: the survey ends with targets still unobserved, and the table gives what "
    "one minute is worth on average elsewhere. ")


def _ask(system, user, max_tokens=8):
    body = {"model": _config()["MODEL_NAME"].strip(), "messages": [{"role": "system", "content": system}, {"role": "user", "content": user},
                                                                    {"role": "assistant", "content": "ANSWER:", "prefix": True}],
            "max_tokens": 4, "temperature": 0, "thinking": {"type": "disabled"}}
    began = time.perf_counter()
    text = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(_config()["MODEL_BASE_URL"].strip().rstrip("/") + "/beta/chat/completions", data=json.dumps(body).encode(),
                                         headers={"content-type": "application/json", "authorization": "Bearer " + _config()["MODEL_API_KEY_ENV"].strip()})
            with urllib.request.urlopen(req, timeout=20) as r:
                text = "ANSWER: " + (json.load(r)["choices"][0]["message"]["content"] or "")
            break
        except Exception as error:  # noqa: BLE001
            if dstats["errors"] < 2: print("duration chooser error", repr(error)[:200], file=sys.stderr)
            dstats["errors"] += 1
            time.sleep(1 + attempt)
    dstats["wait_s"] += time.perf_counter() - began
    return text


def choose_duration(curve, chosen, price, hours_left, n_targets):
    """curve: [(seconds, effective seconds, expected points, science)]; returns the duration to use."""
    rows = sorted({(d, total) for d, eff, total, _ in curve if eff > 0.0})
    if len(rows) < 2:
        return chosen
    dstats["calls"] += 1
    index = next((k for k, (d, _) in enumerate(rows) if d == chosen), None)
    if index is None:
        return chosen
    def near(factor):
        want = chosen * factor
        return min(range(len(rows)), key=lambda k: abs(rows[k][0] - want))
    head = (f"Field with {n_targets} targets. {hours_left:.1f} hours of the night remain. One minute elsewhere is worth about {price * 60:.2f} points.\n")
    if DMODE == "rel":
        lo, hi, now = rows[near(0.67)], rows[near(1.5)], rows[index]
        user = (head + f"SHORTER: {lo[0] / 60:.1f} min, expected {lo[1]:.2f} points\nSAME: {now[0] / 60:.1f} min, expected {now[1]:.2f} points\n"
                f"LONGER: {hi[0] / 60:.1f} min, expected {hi[1]:.2f} points\nReply with exactly one line: ANSWER: SHORTER, ANSWER: SAME or ANSWER: LONGER.")
        text = (_ask(D_SYSTEM, user, 150) or "").upper()
        found = re.findall(r"ANSWER:\s*(SHORTER|SAME|LONGER)", text)
        word = found[-1] if found else "SAME"
        new = lo[0] if word == "SHORTER" else hi[0] if word == "LONGER" else chosen
    else:
        keep = sorted({near(f) for f in (0.4, 0.6, 0.8, 1.0, 1.3, 1.7, 2.5)} | {index})
        table = "\n".join(f"{chr(65 + k)}: {rows[i][0] / 60:.1f} min, expected {rows[i][1]:.2f} points" for k, i in enumerate(keep))
        user = head + f"{table}\nReply with exactly one line: ANSWER: <letter of the exposure length to use>."
        text = (_ask(D_SYSTEM, user, 150) or "").upper()
        found = re.findall(r"ANSWER:\s*([A-H])", text)
        k = ord(found[-1]) - 65 if found else -1
        if os.environ.get('BASE_DURATION_DEBUG') and dstats['calls'] <= 3: print('DBG', repr(text[-40:]), 'planner letter', chr(65 + keep.index(index)), '\n' + table, file=sys.stderr)
        new = rows[keep[k]][0] if 0 <= k < len(keep) else chosen
    if new != chosen:
        dstats["changed"] += 1
        dstats["longer" if new > chosen else "shorter"] += 1
    if dstats["calls"] % 300 == 0:
        print(f"duration chooser: {dstats}", file=sys.stderr, flush=True)
    return new
