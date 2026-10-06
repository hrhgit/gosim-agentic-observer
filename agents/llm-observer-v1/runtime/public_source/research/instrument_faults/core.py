"""Causal, public-only evidence and features shared by training and prediction."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from research.observing_conditions.features import utc, KINDS, DIRECTIONS, notice_matrix

FEATURE_VERSION = "fault-public-history-v1"
PROGRAMS = ("DARK", "BRIGHT", "BACKUP")
STATS = ("ratio", "delta", "delta_std", "below_lower", "zero_fraction", "miss_fraction",
         "saturated_fraction", "reference", "observed", "q_mean", "q_median", "q_width",
         "usable", "blocked", "p_dark", "p_bright", "p_backup", "flux", "weight",
         "duration", "altitude", "az_sin", "az_cos", "assigned", "hits", "comparable",
         "program_dark", "program_bright", "program_backup")
HISTORY_STATS = ("ratio", "delta", "delta_std", "below_lower", "zero_fraction", "miss_fraction",
                 "saturated_fraction", "q_width", "usable", "blocked")
FEATURE_NAMES = list(STATS)
for window in (3, 10, 30):
    FEATURE_NAMES += [f"last{window}:{name}:{stat}" for name in HISTORY_STATS for stat in ("mean", "std")]
    FEATURE_NAMES += [f"last{window}:{name}" for name in ("count", "ratio_trend", "low_run", "elapsed")]
for nights in (1, 3, 7):
    FEATURE_NAMES += [f"nights{nights}:{name}" for name in ("ratio", "delta", "count")]
FEATURE_NAMES += [f"direction:{d}:{s}" for d in DIRECTIONS[:8] for s in ("ratio", "count")]
FEATURE_NAMES += [f"current:{k}:{d}" for k in KINDS for d in DIRECTIONS]
FEATURE_NAMES += ["evidence_exposures", "evidence_targets", "direction_count", "evidence_age",
                  "quake_age", "quake_nights", "repair_age", "false_report_age"]


def finite_stats(values):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    return (float(a.mean()), float(a.std())) if len(a) else (math.nan, math.nan)


def age(now, then):
    return math.log1p(max(0.0, (now - then).total_seconds())) / math.log1p(7 * 86400) if then else math.nan


def reference_scores(target, prediction, duration, program, score):
    """A comparison proxy, NOT E[capped score] and NOT a score prediction interval."""
    q = np.asarray(prediction[:4], float)
    if not np.isfinite(prediction).all() or np.any(q < 0) or q[1] > q[2] or q[2] > q[3]:
        raise ValueError("invalid weather prediction")
    probabilities = np.asarray(prediction[6:9], float)
    if np.any(probabilities < 0) or not np.isclose(probabilities.sum(), 1, atol=1e-5):
        raise ValueError("weather band probabilities must sum to one")
    scale = float(target["feature_flux"]) * duration / (score["flux_zero_point"] * score["exposure_zero_point_seconds"])
    weight = float(target["science_weight"])
    matching = score["program"]["multipliers"][program]
    mismatch = score["program"]["mismatch_multiplier"]
    multiplier = mismatch + probabilities[PROGRAMS.index(program)] * (matching - mismatch)
    reference = weight * min(scale * q[0], 1) * multiplier
    lower = weight * min(scale * q[1], 1) * min(matching, mismatch)
    upper = weight * min(scale * q[3], 1) * max(matching, mismatch)
    return reference, lower, upper, scale * q[0] >= 1


def summarize(record, score):
    predictions = {p["target_id"]: p for p in record["predictions"]}
    pairs = [(h,predictions[h['target_id']]) for h in record['result']['hits']
             if h['target_id'] in predictions and predictions[h['target_id']].get('prediction') is not None
             and predictions[h['target_id']]['target']['science_weight']>0]
    assigned=record['result']['assigned_count'];hit_count=record['result']['hit_count']
    if pairs:
        w=np.asarray([p['prediction'] for _,p in pairs],float)
        if w.shape!=(len(pairs),9) or not np.isfinite(w).all() or np.any(w[:,:4]<0) or np.any(w[:,1]>w[:,2]) or np.any(w[:,2]>w[:,3]):
            raise ValueError('invalid weather prediction shape/quantiles')
        if np.any(w[:,6:9]<0) or not np.allclose(w[:,6:9].sum(1),1,atol=1e-5) or np.any((w[:,4:6]<0)|(w[:,4:6]>1)):
            raise ValueError('invalid weather probabilities')
        flux=np.array([p['target']['feature_flux'] for _,p in pairs]);weights=np.array([p['target']['science_weight'] for _,p in pairs])
        observed=np.array([h['score'] for h,_ in pairs])/weights
        if not np.isfinite(observed).all() or np.any(observed<0):
            raise ValueError('invalid actual feedback score')
        scale=flux*record['duration']/(score['flux_zero_point']*score['exposure_zero_point_seconds'])
        matching=score['program']['multipliers'][record['program']];mismatch=score['program']['mismatch_multiplier']
        reference=np.minimum(scale*w[:,0],1)*(mismatch+w[:,6+PROGRAMS.index(record['program'])]*(matching-mismatch))
        lower=np.minimum(scale*w[:,1],1)*min(matching,mismatch)
        comparable=reference>1e-6;ratio=observed[comparable]/reference[comparable];delta=observed-reference
        stats=[float(np.median(ratio)) if len(ratio) else math.nan,float(delta.mean()),float(delta.std()),float((observed<lower-1e-6).mean()),
               float((observed==0).mean()),1-hit_count/assigned if assigned else math.nan,float((scale*w[:,0]>=1).mean()),float(reference.mean()),float(observed.mean())]
        stats+=np.r_[w[:,0].mean(),w[:,2].mean(),(w[:,3]-w[:,1]).mean(),w[:,4:9].mean(0)].tolist()
        mean_flux,mean_weight,count=float(flux.mean()),float(weights.mean()),int(comparable.sum())
    else:
        stats=[math.nan]*17;stats[5]=1-hit_count/assigned if assigned else math.nan
        mean_flux,mean_weight,count=math.nan,math.nan,0
    az=math.radians(record['azimuth'])
    stats += [mean_flux,mean_weight,record['duration']/3600,record['altitude']/90,math.sin(az),math.cos(az),assigned,hit_count,count]
    stats += [int(record['program']==program) for program in PROGRAMS]
    return dict(zip(STATS,stats))


def matrix_stats(array):
    valid=np.isfinite(array);counts=valid.sum(0)
    means=np.divide(np.where(valid,array,0).sum(0),counts,out=np.full(array.shape[1],np.nan),where=counts>0)
    variance=np.divide(np.where(valid,(array-means)**2,0).sum(0),counts,out=np.full(array.shape[1],np.nan),where=counts>0)
    return np.column_stack([means,np.sqrt(variance)]).ravel().tolist()


@dataclass
class Evidence:
    """Only public observation records and already delivered messages enter this state."""
    score: dict
    weather_version: str | None
    records: list = field(default_factory=list)
    nights: list = field(default_factory=list)
    current_notices: list = field(default_factory=list)
    quake_time: datetime | None = None
    quake_night: int | None = None
    repair_time: datetime | None = None
    false_time: datetime | None = None
    confirmed_time: datetime | None = None
    seen_messages: set = field(default_factory=set)
    latest_bulletin_time: datetime | None = None
    latest_observe_index: int = -1
    latest_observation_end: datetime | None = None

    def messages(self, messages, now):
        now = utc(now)
        for message in sorted(messages, key=lambda m: utc(m["issued_at_utc"])):
            issued = utc(message["issued_at_utc"])
            if issued > now:
                raise ValueError("future message entered fault evidence")
            identity = repr(message)
            if identity in self.seen_messages:
                continue
            self.seen_messages.add(identity)
            kind = message["record_type"]
            if kind == "bulletin":
                if self.latest_bulletin_time and issued < self.latest_bulletin_time:
                    continue
                self.current_notices = message["notices"]
                self.latest_bulletin_time = issued
                if message.get('night_id') and message['night_id'] not in self.nights:
                    self.nights.append(message['night_id'])
                if any(n["event_kind"] == "earthquake" for n in self.current_notices):
                    self.quake_time = issued
                    self.quake_night = len(self.nights)
                    self.quake_active = True
                else:
                    self.quake_active = False
            elif kind == "report_result":
                self.confirmed_time = max(self.confirmed_time,issued) if self.confirmed_time else issued
                if message["correct"]:
                    self.records = [r for r in self.records if utc(r['end'])>issued]
                    self.repair_time = max(self.repair_time,issued) if self.repair_time else issued
                else:
                    self.false_time = max(self.false_time,issued) if self.false_time else issued
            elif kind == "state_resync":
                window = message["invalidated_window"]
                first, last = window["action_index_start"], window["action_index_end_exclusive"]
                self.records = [r for r in self.records if not first <= r["observe_index"] < last]

    def observation(self, record):
        required = {"observe_index", "start", "end", "night", "duration", "program", "altitude", "azimuth", "predictions", "result"}
        if set(record) != required:
            raise ValueError(f"public observation keys mismatch: {set(record)^required}")
        if record["observe_index"] <= self.latest_observe_index:
            raise ValueError("duplicate or out-of-order observation")
        if utc(record["end"]) <= utc(record["start"]):
            raise ValueError("invalid exposure time")
        if self.latest_observation_end and utc(record["start"]) < self.latest_observation_end:
            raise ValueError("overlapping observation history")
        if record["program"] not in PROGRAMS or not np.isfinite([record["duration"],record["altitude"],record["azimuth"]]).all():
            raise ValueError("invalid public observation")
        for prediction in record['predictions']:
            if prediction.get('weather_version') != self.weather_version:
                raise ValueError('prediction came from a different weather model')
            if utc(prediction['issued_at_utc']) > utc(record['start']):
                raise ValueError('weather prediction must precede exposure')
            if prediction['duration_seconds'] != record['duration']:
                raise ValueError('prediction exposure duration differs')
        self.latest_observe_index = record['observe_index']
        self.latest_observation_end = utc(record['end'])
        if record["night"] not in self.nights:
            self.nights.append(record["night"])
        stats=summarize(record,self.score)
        paired={p['target_id'] for p in record['predictions'] if p.get('prediction') is not None}
        matched=sum(hit['target_id'] in paired for hit in record['result']['hits'])
        self.records.append(dict(record,stats=stats,numeric=np.fromiter(stats.values(),float),matched_targets=matched))

    def vector(self, now):
        now = utc(now)
        if self.latest_observation_end and self.latest_observation_end > now:
            raise ValueError("future observation entered fault evidence")
        vector = list(self.records[-1]["stats"].values()) if self.records else [math.nan]*len(STATS)
        recent=self.records[-30:]
        matrix=np.array([r['numeric'] for r in recent]) if recent else np.empty((0,len(STATS)))
        history_indices=[STATS.index(name) for name in HISTORY_STATS]
        for window in (3, 10, 30):
            rows = recent[-window:]
            vector.extend(matrix_stats(matrix[-window:,history_indices]))
            comparable = [r for r in rows if np.isfinite(r["stats"]["ratio"])]
            trend = (comparable[-1]["stats"]["ratio"]-comparable[0]["stats"]["ratio"]) if len(comparable)>1 else math.nan
            low = 0
            for r in reversed(comparable):
                if r["stats"]["ratio"] >= 0.7:
                    break
                low += 1
            elapsed = age(utc(rows[-1]["end"]), utc(rows[0]["end"])) if rows else math.nan
            vector.extend([len(comparable), trend, low, elapsed])
        for number in (1, 3, 7):
            keep = set(self.nights[-number:])
            rows = []
            for r in reversed(self.records):
                if r['night'] not in keep:
                    break
                rows.append(r)
            vector += [finite_stats([r["stats"]["ratio"] for r in rows])[0], finite_stats([r["stats"]["delta"] for r in rows])[0], len(rows)]
        last = self.records[-30:]
        for direction in range(8):
            rows = [r for r in last if int((r["azimuth"]+22.5)//45)%8 == direction and r["stats"]["comparable"]]
            vector += [finite_stats([r["stats"]["ratio"] for r in rows])[0], len(rows)]
        vector += notice_matrix(self.current_notices).ravel().tolist()
        info = self.info(now)
        quake_nights = len(self.nights)-self.quake_night if self.quake_night is not None else math.nan
        vector += [info["evidence_exposure_count"], info["evidence_target_count"], info["direction_count"],
                   age(now, utc(last[-1]["end"])) if last else math.nan, age(now, self.quake_time),
                   quake_nights, age(now, self.repair_time), age(now, self.false_time)]
        assert len(vector) == len(FEATURE_NAMES)
        return np.asarray(vector, np.float32)

    def info(self, now):
        now = utc(now)
        if self.latest_observation_end and self.latest_observation_end>now:
            raise ValueError('future observation entered fault evidence')
        rows = [r for r in self.records[-30:] if r["stats"]["comparable"] > 0]
        count = len(rows)
        status = "ready" if count >= 3 else "limited_evidence" if count else "no_observation_evidence"
        p = None
        if not self.weather_version:
            status = "weather_unavailable"
        if self.confirmed_time == now:
            status, p = "confirmed_current_state", 0.0
        return {"fault_probability": p, "evidence_exposure_count": count,
                "evidence_target_count": int(sum(r['matched_targets'] for r in rows)),
                "direction_count": len({int((r["azimuth"]+22.5)//45)%8 for r in rows}),
                "last_evidence_age_seconds": (now-utc(rows[-1]["end"])).total_seconds() if rows else None,
                "status": status}

    def rule(self):
        rows = [r for r in self.records if r["stats"]["comparable"]][-3:]
        quake_clear = self.quake_night is None or len(self.nights)-self.quake_night >= 4
        return len(rows) == 3 and quake_clear and all(r["stats"]["ratio"] < 0.7 and r["stats"]["blocked"] < 0.3 for r in rows)
