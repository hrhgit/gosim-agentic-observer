from __future__ import annotations
import numpy as np
from pathlib import Path
from .features import utc
DEFAULT = Path(__file__).resolve().parents[3]/"models/conditions-reliability"

def freshness(now_utc, history, nights):
    now = utc(now_utc)
    current = next((n for n in nights if utc(n['observing_start_utc']) <= now < utc(n['observing_end_utc'])), None)
    if current is None:
        raise ValueError('decision must be within a public observing night')
    completed = [h for h in history if utc(h['end_utc']) <= now][-8:]
    start = utc(current['observing_start_utc'])
    fresh = sum((start <= utc(h['end_utc']) <= now for h in completed))
    return dict(all_history_count=len(completed), fresh_history_count=fresh, history_count=fresh, never_observed=len(completed) == 0)
