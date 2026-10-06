"""Incremental published-message cache with content-based invalidation.

Maintains the exact PublicTimeline arrays. Edits, reordering or older inserted
messages rebuild the index; adding messages extends only the new rows.
"""
from __future__ import annotations
import numpy as np
from .features import PublicTimeline,notice_matrix,utc


def signature(records,forecast=False):
    return tuple((r['issued_at_utc'],r.get('coverage_start_utc'),r.get('coverage_end_utc'),
        tuple((n['event_kind'],n['direction'],tuple(n.get('nights',[]))) for n in r['notices'])) for r in records)


class PublicCache:
    def __init__(self):
        self.timeline=PublicTimeline([],[]);self.bulletin_signature=();self.forecast_signature=()

    def sync(self,bulletins,forecasts):
        bs,fs=signature(bulletins),signature(forecasts,True)
        if bs==self.bulletin_signature and fs==self.forecast_signature:return self.timeline
        old=self.bulletin_signature;of=self.forecast_signature
        ordered=all(a[0]<=b[0] for a,b in zip(bs,bs[1:])) and all(a[0]<=b[0] for a,b in zip(fs,fs[1:]))
        if not ordered or bs[:len(old)]!=old or fs[:len(of)]!=of:
            self.timeline=PublicTimeline(bulletins,forecasts)
        else:
            t=self.timeline;new=bulletins[len(old):]
            if new:
                matrices=np.array([notice_matrix(b['notices']).ravel() for b in new])
                times=np.array([utc(b['issued_at_utc']).timestamp() for b in new])
                previous=t.matrices[-1] if len(t.matrices) else np.zeros(72)
                starts=t.starts[-1].copy() if len(t.starts) else np.zeros(72)
                rows=[]
                for timestamp,matrix in zip(times,matrices):
                    starts=np.where(matrix>0,np.where(previous>0,starts,timestamp),np.nan)
                    rows.append(starts);previous=matrix
                terrain=matrices[:,63:72].copy()
                if len(t.terrain):terrain[0]=np.maximum(terrain[0],t.terrain[-1])
                terrain=np.maximum.accumulate(terrain,axis=0)
                t.times=np.r_[t.times,times];t.matrices=np.vstack([t.matrices,matrices])
                t.starts=np.vstack([t.starts,np.array(rows)]);t.terrain=np.vstack([t.terrain,terrain])
                t.bulletins=t.bulletins+list(new)
            t.forecasts=t.forecasts+list(forecasts[len(of):])
            t.forecast_times.extend(utc(f['issued_at_utc']).timestamp() for f in forecasts[len(of):])
        self.bulletin_signature=bs;self.forecast_signature=fs;return self.timeline
