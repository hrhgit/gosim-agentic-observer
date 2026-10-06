"""Independent synthetic scenes and reusable causal exposure physics.

Physics is scorer-side only. Public weather features and agent records are separate.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from challenge import v4_weather_simulator as sim
from challenge.contracts import NIGHT_COLUMNS, SLOT_COLUMNS, WEATHER_COLUMNS, write_exact_csv
from challenge.v4_fiber_map import FiberGrid, radec_to_altaz
from challenge.v4_runner import load_scenario, _pointing_offset
from challenge.v4_scorer import WeatherTruth, DIRECTIONAL_MULTIPLIER_TYPES, FORCE_CLOSE_TYPE, ZERO_SCORE_TYPE
from research.observing_conditions.data import save_json, source_hashes, pieces
from research.observing_conditions.features import PublicTimeline, candidate_features, geometry, altaz, minimum_altitude, utc, CANDIDATE_NAMES
from . import VERSION
from .core import PROGRAMS

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts/instrument_faults"
DURATIONS = (60, 300, 900, 1800, 3600)


def entries(pilot=False):
    result = []
    for ci, card in enumerate("abcd"):
        if pilot:
            for index in range(2):
                result.append(dict(id=f"dev-{card}-{index}", card=card, split="development", seed=8900000+ci*100+index,
                                   fault_count=(0, 3)[index], uniform_onset=True, quake=bool(index)))
            continue
        splits = ["train"]*80+["validation"]*16+["calibration"]*16+["test"]*16
        for index, split in enumerate(splits):
            # Orthogonal, balanced across each split, rather than coupling quake/onset to labels.
            block = index % 16
            result.append(dict(id=f"fault-{card}-{index:03}", card=card, split=split,
                               seed=8100000+ci*10000+index, fault_count=(0, 1, 1, 3)[block%4],
                               uniform_onset=bool((block//4)%2), quake=bool(block//8)))
        for group in ("weak_fault", "weather_shift", "quake_overlap", "sparse_stress"):
            for index in range(4):
                gi = ("weak_fault", "weather_shift", "quake_overlap", "sparse_stress").index(group)
                result.append(dict(id=f"external-{card}-{group}-{index}", card=card, split="external", group=group,
                                   seed=8500000+ci*10000+gi*100+index, fault_count=1,
                                   uniform_onset=True, quake=group in ("quake_overlap", "sparse_stress")))
    return result


def read_public(card):
    root = ROOT / "cards" / card
    fiber = json.loads((root/"config/v4_fiber_config.json").read_text())
    site = dict(fiber["site"], minimum_altitude_deg=30.0)
    score = json.loads((root/"config/v4_score_config.json").read_text())
    nights = list(csv.DictReader((root/"public/v4_night_calendar.csv").open()))
    targets = list(csv.DictReader((root/"public/targets.csv").open()))
    for target in targets:
        for field in ("ra_deg", "dec_deg", "feature_flux", "science_weight"):
            target[field] = float(target[field])
        target["required"] = target["required"].lower() == "true"
    return site, fiber, score, nights, targets


def configure(entry, site, calendar):
    config = json.loads((ROOT/"challenge/reference/v4/v4_weather_config.json").read_text())
    config.update(seed=entry["seed"], seed_derivation="sha256-v1")
    config["site"] = {k:v for k,v in site.items() if k != "minimum_altitude_deg"}
    from datetime import date
    config["survey"].update(start_date=calendar[0]["night_date"],
                            end_date=str(date.fromisoformat(calendar[-1]["night_date"])+timedelta(days=1)))
    rng = np.random.default_rng(entry["seed"])
    spread = 0.25 if entry.get("group") == "weather_shift" else 0.1
    bounds = (0.5, 1.5) if spread == 0.25 else (0.75, 1.25)
    for name in ("seeing_arcsec", "transparency", "sky_quality"):
        definition = config["quality"][name]
        for parameter in ("nominal", "seasonal_amplitude", "night_sigma", "slot_sigma"):
            definition[parameter] *= float(rng.uniform(1-spread, 1+spread))
        definition["nominal"] = float(np.clip(definition["nominal"], definition["minimum"], definition["maximum"]))
    config["quality"].update(night_correlation=float(rng.uniform(.7, .9)), slot_correlation=float(rng.uniform(.8, .95)))
    ratio = len(calendar)/124
    for definition in config["weather_events"]["conditions"].values():
        definition["count"] = max(1, round(definition["count"]*ratio*rng.uniform(*bounds)))
        multiplier = rng.uniform(*bounds)
        definition["duration_slots"] = [max(1, round(v*multiplier)) for v in definition["duration_slots"]]
    config["rocket_launch"]["count"] = max(1, round(4*ratio))
    config["earthquake"]["count"] = max(1, round(2*ratio)) if entry["quake"] else 0
    config["instrument_fault"].update(count=entry["fault_count"], instrument_efficiency_multiplier_range=
                                      [0.75, 0.90] if entry.get("group") == "weak_fault" else [0.4, 0.7])
    config["stress_tests"]["enabled"] = entry["card"] == "d" or entry.get("group") == "sparse_stress"
    sim.validate_config(config)
    return config


def generate_environment(entry, directory):
    site, fiber, score, calendar, targets = read_public(entry["card"])
    config = configure(entry, site, calendar)
    nights, slots = sim.build_nights(config)
    generated = [n.csv_row() for n in nights]
    if [(n["night_id"], n["observing_start_utc"], n["observing_end_utc"]) for n in generated] != [
            (n["night_id"], n["observing_start_utc"], n["observing_end_utc"]) for n in calendar]:
        raise ValueError("generated calendar differs from authoritative formal public calendar")
    events, effects = sim.generate_events(config, nights, slots)
    faults = [e for e in events if e.event_type == "instrument_fault"]
    if entry["uniform_onset"] and faults:
        rng = np.random.default_rng(entry["seed"]+400000)
        indices = sorted(rng.choice(len(slots), len(faults), replace=False).tolist())
        if entry.get("group") == "quake_overlap":
            quake = next(e for e in events if e.event_type == "earthquake")
            indices[0] = next(i for i,s in enumerate(slots) if s.timestamp_utc >= quake.actual_start_utc)
        ends = [slots[i].timestamp_utc for i in indices[1:]]+[slots[-1].timestamp_utc+timedelta(seconds=slots[-1].duration_seconds)]
        replacements = {f.event_id: replace(f, actual_start_utc=slots[i].timestamp_utc, actual_end_utc=end)
                        for f,i,end in zip(faults, indices, ends)}
        events = sorted([replacements.get(e.event_id,e) for e in events], key=lambda e:(e.actual_start_utc,e.event_id))
    if config["stress_tests"]["enabled"]:
        config["stress_tests"]["data_loss"].update(trigger="fixed_slot", slot_id=slots[len(slots)//2].slot_id)
    sim.validate_config(config)
    save_json(directory/"weather_config.json", config)
    source = directory/"environment"
    source.mkdir(parents=True, exist_ok=True)
    write_exact_csv(source/"v4_night_calendar.csv", NIGHT_COLUMNS, (n.csv_row() for n in nights))
    write_exact_csv(source/"v4_slots.csv", SLOT_COLUMNS, (s.csv_row() for s in slots))
    write_exact_csv(source/"v4_events.csv", sim.V4_EVENT_COLUMNS, (e.csv_row() for e in events))
    write_exact_csv(source/"v4_earthquake_effects.csv", sim.V4_EARTHQUAKE_EFFECT_COLUMNS, (e.csv_row() for e in effects))
    write_exact_csv(source/"v4_weather_truth.csv", WEATHER_COLUMNS, sim.generate_weather(config,nights,slots,events,effects))
    for name, lines in (("v4_bulletins.jsonl", sim.generate_bulletins(config,nights,slots,events,effects)),
                        ("v4_forecasts.jsonl", sim.generate_forecasts(config,nights,events))):
        (source/name).write_text("\n".join(lines)+"\n")
    stress = sim.generate_stress_events(config, events)
    if stress:
        write_exact_csv(source/"v4_stress_events.csv", sim.V4_STRESS_EVENT_COLUMNS, stress)
    public = ROOT/"cards"/entry["card"]
    products = {"targets_csv": str(public/"public/targets.csv"), "footprint_csv": str(public/"public/footprint.csv"),
                "slots_csv": "environment/v4_slots.csv", "weather_truth_csv": "environment/v4_weather_truth.csv",
                "events_csv": "environment/v4_events.csv", "bulletins_jsonl": "environment/v4_bulletins.jsonl",
                "forecasts_jsonl": "environment/v4_forecasts.jsonl", "observation_requests_jsonl": "empty_requests.jsonl"}
    (directory/"empty_requests.jsonl").write_text("")
    save_json(directory/"scenario.json", dict(schema_version="v4-scenario-v1", name=entry["id"], site=site,
             minimum_altitude_deg=30, fiber_config=str(public/"config/v4_fiber_config.json"),
             score_config=str(public/"config/v4_score_config.json"), products=products,
             stress={"enabled": bool(stress), "stress_events_csv": "environment/v4_stress_events.csv"}))
    return load_scenario(directory/"scenario.json"), calendar


def unit_vectors(coords):
    ra, dec = np.radians(coords).T
    return np.column_stack([np.cos(dec)*np.cos(ra), np.cos(dec)*np.sin(ra), np.sin(dec)])


def schedule(scenario, calendar, entry, track, skipped=None):
    """Only public geometry and target attributes choose actions, never weather truth."""
    targets, site = scenario.targets, scenario.site
    coords = np.array([[t["ra_deg"], t["dec_deg"]] for t in targets])
    weights = np.array([t["science_weight"] for t in targets])
    vectors = unit_vectors(coords)
    tree = cKDTree(vectors)
    grid = FiberGrid.from_config(scenario.fiber_config)
    rng = np.random.default_rng(entry["seed"]+1000000+track)
    radius = 2*math.sin(math.radians(grid.fov_side_deg/math.sqrt(2)+0.3)/2)
    previous = None
    rows = []
    skipped=[] if skipped is None else skipped
    attempts = 2 if entry.get("group") == "sparse_stress" else 8
    for night in calendar:
        first, last = utc(night["observing_start_utc"]), utc(night["observing_end_utc"])
        cursor = first
        for anchor in range(attempts):
            start = max(cursor, first+timedelta(seconds=int((last-first).total_seconds()*anchor/attempts)))
            if start >= last:
                skipped.append(dict(night=night['night_id'],candidate=anchor,reason='previous exposure reached night end'))
                continue
            requested = int(rng.choice(DURATIONS)); end = min(start+timedelta(seconds=requested), last)
            duration = int((end-start).total_seconds())
            eligible = np.flatnonzero(minimum_altitude(coords[:,0], coords[:,1], start,end,site)>=30)
            if not len(eligible):
                skipped.append(dict(night=night['night_id'],candidate=anchor,reason='no public target above minimum altitude'))
                continue
            alts, azs = altaz(coords[eligible,0],coords[eligible,1],start,site)
            if track and len(rows)%3 == 2 and previous in eligible:
                center = previous
            elif track:
                ranking = np.argsort(-(alts/90+weights[eligible]/max(weights.max(),1e-9)), kind="stable")
                center = int(eligible[ranking[0]])
            else:
                center = int(rng.choice(eligible))
            previous = center
            alt, az = radec_to_altaz(*coords[center],start,site["latitude_deg"],site["longitude_deg"])
            nearby = tree.query_ball_point(vectors[center], radius)
            assignments = {}
            for index in sorted(nearby, key=lambda i:(-weights[i],targets[i]["target_id"])):
                if minimum_altitude(coords[index:index+1,0],coords[index:index+1,1],start,end,site)[0]<30:
                    continue
                result = grid.classify_target(*coords[index],start,alt,az,site["latitude_deg"],site["longitude_deg"])
                if result.region == "glass" and result.fiber_id not in assignments:
                    assignments[result.fiber_id] = index
            rows.append(dict(observe_index=len(rows), start=start.isoformat(), end=end.isoformat(), night=night["night_id"],
                             duration=duration, requested=requested, program=PROGRAMS[len(rows)%3], altitude=alt,
                             azimuth=az, assignments={str(k):targets[v]["target_id"] for k,v in assignments.items()},
                             target_indices=list(assignments.values()), night_end=last.isoformat(), night_date=night["night_date"]))
            cursor = end
    return rows


def coefficients(scenario, rows):
    """Exact official midpoint physics, factored into repaired/unrepaired contributions.

    Reports are between exposures, so this cache recomputes future scores after repair
    without re-integrating the same geometry for every threshold combination.
    """
    faults = [e for e in scenario.events if e.event_type == "instrument_fault"]
    weather = WeatherTruth(scenario.slots, faults)
    directional = [e for e in scenario.events if e.scope_type == "HORIZON_SECTOR"]
    grid = FiberGrid.from_config(scenario.fiber_config)
    offset_alt, offset_az = _pointing_offset(scenario)
    timeline = PublicTimeline(scenario.bulletins, scenario.forecasts)
    contexts, histories, candidates, raw, repaired, bands, environments, hit_mask, pointers = [], [], [], [], [], [], [], [], [0]
    for row in rows:
        start,end = utc(row["start"]),utc(row["end"])
        elapsed=(end-start).total_seconds()
        targets = [scenario.targets[i] for i in row["target_indices"]]
        ra=np.array([t["ra_deg"] for t in targets]); dec=np.array([t["dec_deg"] for t in targets])
        n=len(targets)
        hits=[]
        for fiber, index in zip(row["assignments"],row["target_indices"]):
            target=scenario.targets[index]
            result=grid.classify_target(target["ra_deg"],target["dec_deg"],start,row["altitude"],row["azimuth"],
                                       scenario.site["latitude_deg"],scenario.site["longitude_deg"],offset_alt,offset_az)
            hits.append(result.region=="glass" and result.fiber_id==int(fiber))
        a=np.zeros((n,len(faults)+1)); b=a.copy(); band=np.zeros(n);environment=np.zeros(n)
        # The official scorer splits directional boundaries and weather slots;
        # fault state is read at each midpoint, so do not add extra fault cuts.
        active=[e for e in directional if e.overlaps(start,end)]
        for moment,seconds,truth in pieces(weather,active,start,end,True):
            if truth is None or not truth.is_observable:
                continue
            _,alt,az,g=geometry(ra,dec,moment,scenario.site,scenario.score_config)
            valid=np.ones(n,bool); multiplier=np.ones(n)
            for e in directional:
                if not e.actual_start_utc<=moment<e.actual_end_utc:
                    continue
                az_ok=((az>=e.azimuth_start_deg)&(az<=e.azimuth_end_deg)) if e.azimuth_start_deg<=e.azimuth_end_deg else ((az>=e.azimuth_start_deg)|(az<=e.azimuth_end_deg))
                covered=az_ok&(alt>=e.min_altitude_deg)&(alt<=e.max_altitude_deg)
                if e.force_close or e.event_type in (ZERO_SCORE_TYPE,FORCE_CLOSE_TYPE):
                    valid &= ~covered
                if e.event_type in DIRECTIONAL_MULTIPLIER_TYPES:
                    multiplier *= np.where(covered,e.transparency_multiplier*e.sky_quality_multiplier*e.instrument_efficiency_multiplier/e.seeing_multiplier,1)
            coefficient=seconds*valid*g*(truth.transparency*truth.sky_quality/truth.seeing_arcsec)*multiplier/elapsed
            environment+=coefficient
            fault=weather.active_fault(moment)
            fi=1+faults.index(fault) if fault else 0
            a[:,fi] += coefficient*truth.instrument_efficiency
            b[:,fi] += coefficient*(min(1,truth.instrument_efficiency/fault.instrument_efficiency_multiplier) if fault else truth.instrument_efficiency)
        for moment,seconds,truth in pieces(weather,[],start,end,False):
            if truth and truth.is_observable:
                band += seconds*geometry(ra,dec,moment,scenario.site,scenario.score_config)[-1]*(truth.transparency*truth.sky_quality/truth.seeing_arcsec)/elapsed
        context,history=timeline.snapshot(start,start,row["requested"],row["duration"],row["night_date"],end if not row.get("night_end") else utc(row["night_end"]),scenario.site,scenario.score_config)
        candidate,_=candidate_features(ra,dec,start,end,scenario.site,scenario.score_config)
        contexts.append(context);histories.append(history);candidates.append(candidate)
        raw.append(a); repaired.append(b); bands.append(band);environments.append(environment); hit_mask.extend(hits);pointers.append(pointers[-1]+n)
    counts=np.diff(pointers); maximum=int(counts.max(initial=0))
    padded=np.zeros((len(rows),maximum,len(CANDIDATE_NAMES)),np.float32)
    for i,c in enumerate(candidates):
        padded[i,:len(c)]=c
    return {"raw":np.concatenate(raw),"repaired":np.concatenate(repaired),"band":np.concatenate(bands),"environment":np.concatenate(environments),
            "hit":np.asarray(hit_mask,bool),"pointers":np.array(pointers)}, {
                "context":np.asarray(contexts),"history":np.asarray(histories),"candidate":padded,"counts":counts}


def prepare_one(arguments):
    entry,output,sources = arguments
    directory=Path(output)/"scenarios"/entry["id"]
    signature=hashlib.sha256(json.dumps({"entry":entry,"sources":sources,"version":VERSION},sort_keys=True).encode()).hexdigest()
    marker=directory/"prepared.json"
    if marker.exists():
        existing=json.loads(marker.read_text())
        if existing["signature"] != signature:
            raise ValueError("prepared scene signature mismatch")
        for track in (0,1):
            if not (directory/f'skipped-{track}.json').exists():
                scenario=load_scenario(directory/'scenario.json');skipped=[]
                rows=schedule(scenario,read_public(entry['card'])[3],entry,track,skipped)
                if rows!=json.loads((directory/f'schedule-{track}.json').read_text()):
                    raise ValueError('development schedule changed while reconstructing skipped candidates')
                save_json(directory/f'skipped-{track}.json',skipped)
        return existing
    directory.mkdir(parents=True,exist_ok=True)
    scenario,calendar=generate_environment(entry,directory)
    counts=[]
    for track in (0,1):
        skipped=[];rows=schedule(scenario,calendar,entry,track,skipped)
        physics,inputs=coefficients(scenario,rows)
        save_json(directory/f"schedule-{track}.json",rows)
        save_json(directory/f"skipped-{track}.json",skipped)
        np.savez_compressed(directory/f"physics-{track}.npz",**physics)
        np.savez_compressed(directory/f"weather-input-{track}.npz",**inputs)
        counts.append(len(rows))
    complete=dict(**entry,signature=signature,exposures=counts,source="self-generated, NOT official hidden weather")
    save_json(marker,complete)
    return complete


def prepare(output=DEFAULT_OUTPUT, workers=2, pilot=False):
    output=Path(output); scene_entries=entries(pilot)
    sources=source_hashes()
    manifest=dict(version=VERSION,sources=sources,entries=scene_entries,pilot=pilot,
                  source="formal public A-D + explicitly self-generated weather, events and schedules")
    path=output/"manifest.json"
    if path.exists() and json.loads(path.read_text())!=manifest:
        raise ValueError("dataset manifest mismatch; choose a new output directory")
    save_json(path,manifest)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks=[pool.submit(prepare_one,(e,str(output),sources)) for e in scene_entries]
        for count,future in enumerate(as_completed(tasks),1):
            complete=future.result()
            print(f"prepared {count}/{len(tasks)} {complete['id']} {complete['exposures']}",flush=True)
    save_json(output/"preparation_complete.json",dict(scenarios=len(scene_entries)))
