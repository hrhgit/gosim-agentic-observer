#!/usr/bin/env python3
"""Small paired sky measurements, with complete selected public-message capture."""
from bisect import bisect_right
import json
import math
import sys
from astro import Site, RAD, DEG, unit_vector, parse_utc, format_utc
from capture import Capture

DIRECTIONS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def normal(v):
    length = math.sqrt(dot(v, v))
    return tuple(x/length for x in v)


def field_axes(center, zenith):
    sa = dot(center, zenith)
    ca = math.sqrt(max(0, 1-sa*sa))
    if ca < .03:
        return None
    up = tuple((z-sa*c)/ca for z, c in zip(zenith, center))
    right = ((center[1]*zenith[2]-center[2]*zenith[1])/ca,
             (center[2]*zenith[0]-center[0]*zenith[2])/ca,
             (center[0]*zenith[1]-center[1]*zenith[0])/ca)
    return up, right


class Probe:
    def __init__(self, payload):
        config = payload['site']; scoring = payload['scoring']; instrument = payload['instrument']
        self.site = Site(config['latitude_deg'], config['longitude_deg'], config['minimum_altitude_deg'], scoring['airmass_exponent'], scoring.get('lunar_model', {}))
        self.scoring = scoring
        self.minimum = int(instrument['exposure']['min_duration_seconds'])
        self.maximum = int(instrument['exposure']['max_duration_seconds'])
        self.pitch = float(instrument['pitch_deg']); self.glass = float(instrument['glass_side_deg'])
        self.side = int(instrument['grid_side'])
        self.slot = float(payload['survey']['slot_seconds'])
        self.end = parse_utc(payload['survey']['end_utc'])
        self.nights = [(parse_utc(n['observing_start_utc']), parse_utc(n['observing_end_utc'])) for n in payload['survey']['nights']]
        self.night_ends = [n[1] for n in self.nights]
        table = payload['targets']; columns = {c:i for i,c in enumerate(table['columns'])}
        self.targets = {}; self.bins = {}
        for row in table['rows']:
            ra, dec = float(row[columns['ra_deg']]), float(row[columns['dec_deg']])
            tid = row[columns['target_id']]
            target = {'id':tid, 'ra':ra, 'dec':dec, 'vec':unit_vector(ra, dec), 'flux':float(row[columns['feature_flux']])}
            self.targets[tid] = target
            self.bins.setdefault((int(ra//10), int((dec+90)//10)), []).append(target)
        self.cycle = 0; self.next_probe = 0.0; self.last_pair = -1e30
        self.pending = None; self.notices = (); self.notice_key = (); self.last_hits = None
        self.pair_id = 0

    def near(self, vec, radius):
        ra = math.atan2(vec[1], vec[0])*DEG % 360
        dec = math.asin(vec[2])*DEG
        reach = 180 if abs(dec)+radius >= 85 else min(180, radius/max(.05, math.cos((abs(dec)+radius)*RAD)))
        columns = {k % 36 for k in range(math.floor((ra-reach)/10), math.floor((ra+reach)/10)+1)}
        rows = range(max(0, math.floor((dec-radius+90)/10)), min(17, math.floor((dec+radius+90)/10))+1)
        return [target for b in rows for k in sorted(columns) for target in self.bins.get((k,b), ())]

    def plan(self, now, azimuth, altitude, label, pair_id, duration):
        _, zenith, north, east = self.site.frame(now)
        desired = tuple(math.sin(altitude*RAD)*z + math.cos(altitude*RAD)*(math.cos(azimuth*RAD)*n+math.sin(azimuth*RAD)*e) for z,n,e in zip(zenith,north,east))
        candidates = self.near(desired, 12)
        candidates = [t for t in candidates if dot(t['vec'], desired)>=math.cos(12*RAD) and self.site.altaz(t['vec'], now)[0]>=self.site.min_alt+1 and self.site.altaz(t['vec'], now+duration)[0]>=self.site.min_alt+1]
        if not candidates:
            return None, {'pair_id':pair_id, 'role':label, 'desired_alt_deg':altitude, 'desired_az_deg':azimuth, 'coverage':'no eligible public target within 12 degrees'}
        # Nearest available target: a measurement location, not a score-maximizing choice.
        anchor = max(candidates, key=lambda t:dot(t['vec'], desired))
        center = anchor['vec']; axes = field_axes(center, zenith)
        if axes is None:
            return None, {'pair_id':pair_id, 'role':label, 'coverage':'target too close to zenith'}
        # For even grids put the anchor at the middle of a fiber, not on a cell edge.
        if self.side % 2 == 0:
            up, right = axes
            shift = math.tan(.5*self.pitch*RAD)
            center = normal(tuple(c-shift*(u+r) for c,u,r in zip(center,up,right)))
        axes = field_axes(center, zenith); up,right = axes
        pointing_alt, pointing_az = self.site.altaz(center, now)
        nearby = self.near(center, self.side*self.pitch)
        cells = {}
        for target in nearby:
            divisor = dot(target['vec'],center)
            if divisor<=0:continue
            x = dot(target['vec'],right)/divisor*DEG; y = dot(target['vec'],up)/divisor*DEG
            col = math.floor(x/self.pitch+self.side/2); row = math.floor(y/self.pitch+self.side/2)
            if not 0<=col<self.side or not 0<=row<self.side:continue
            cx=(col+.5-self.side/2)*self.pitch; cy=(row+.5-self.side/2)*self.pitch
            margin = min(self.glass/2-abs(x-cx), self.glass/2-abs(y-cy))
            if margin<self.glass*.18:continue
            if min(self.site.altaz(target['vec'],now)[0],self.site.altaz(target['vec'],now+duration)[0])<self.site.min_alt+1:continue
            cell=str(row*self.side+col)
            current=cells.get(cell)
            if current is None or margin>current[1]:cells[cell]=(target,margin)
        anchor_cells=[(k,v) for k,v in cells.items() if v[0]['id']==anchor['id']]
        others=sorted([(k,v) for k,v in cells.items() if v[0]['id']!=anchor['id']],key=lambda v:(-v[1][1],v[0]))
        # Four targets per exposure are the designed measurement sample size.
        chosen=(anchor_cells+others)[:4]
        if not chosen:return None,{'pair_id':pair_id,'role':label,'coverage':'no targets safely inside fibers'}
        action={'action':'observe','pointing':{'alt_deg':pointing_alt,'az_deg':pointing_az},'assignments':{k:v[0]['id'] for k,v in chosen},'duration_seconds':duration,'program':'BACKUP'}
        note={'pair_id':pair_id,'role':label,'desired_alt_deg':altitude,'desired_az_deg':azimuth,'actual_alt_deg':pointing_alt,'actual_az_deg':pointing_az,'coverage':'sampled','probe_action':action,'active_notices':list(self.notices)}
        return action,note

    def decide(self, payload):
        now=parse_utc(payload['now_utc'])
        for message in payload.get('new_messages') or []:
            if message.get('record_type')=='bulletin':
                self.notices=tuple(message.get('notices') or ())
                key=tuple(sorted((n['event_kind'],n['direction']) for n in self.notices))
                if key != self.notice_key and now-self.last_pair>=1800:
                    self.next_probe=min(self.next_probe,now)
                self.notice_key=key
        last=payload.get('last_result') or {}
        if last.get('action')=='observe':
            hits=last.get('hits') or []
            if hits and all(h.get('score',0)==0 for h in hits) and now-self.last_pair>=1800:
                self.next_probe=min(self.next_probe,now)
        night=bisect_right(self.night_ends,now)
        if night>=len(self.nights):return self.wait(now),None
        start,end=self.nights[night]
        if now<start:
            # Keep receiving daylight handover messages at legal hourly intervals.
            return self.wait(now),None
        if end-now<self.minimum:return self.wait(now),None
        if self.pending is not None:
            az,alt,pair_id=self.pending;self.pending=None
            return self.plan_or_wait(now,az,alt,'reference',pair_id,min(self.minimum,int(end-now)))
        if now<self.next_probe:return self.wait(now),None
        self.pair_id+=1;pair_id=self.pair_id
        level=(34,40,55,70)[(self.cycle//8)%4]
        affected=[DIRECTIONS.index(n['direction'])*45 for n in self.notices if n['direction'] in DIRECTIONS and n['event_kind']!='terrain_obstruction']
        az=affected[self.cycle%len(affected)] if affected else (self.cycle%8)*45
        # Opposite compass direction is merely a reference candidate; it can be
        # affected too. Downstream analysis must inspect both targets' notices.
        reference=(az+180)%360
        self.cycle+=1;self.last_pair=now;self.next_probe=now+7200
        self.pending=(reference,level,pair_id)
        return self.plan_or_wait(now,az,level,'affected_or_sweep',pair_id,min(self.minimum,int(end-now)))

    def plan_or_wait(self,now,az,alt,label,pair_id,duration):
        action,note=self.plan(now,az,alt,label,pair_id,duration)
        return (action or self.wait(now)),note

    def wait(self,now):
        remaining=self.end-now
        if 1<remaining<=self.maximum:
            return {'action':'wait','until_utc':format_utc(self.end-1)}
        return {'action':'wait','duration_seconds':self.maximum if remaining>self.maximum else self.minimum}


def main():
    capture=Capture(sys.stderr);probe=None
    try:
        for raw in sys.stdin:
            message=json.loads(raw);kind=message.get('message_type');note=None
            if kind=='initialize':probe=Probe(message['payload'])
            elif kind=='decision_request':
                action,note=probe.decide(message['payload'])
                response={'protocol_version':message['protocol_version'],'message_type':'decision_response','decision_sequence':message['decision_sequence'],**action}
            capture.record(raw,annotations=note)
            if kind=='decision_request':
                sys.stdout.write(json.dumps(response,separators=(',',':'))+'\n');sys.stdout.flush()
            elif kind=='finish':break
    finally:capture.flush()
    return 0

if __name__=='__main__':raise SystemExit(main())
