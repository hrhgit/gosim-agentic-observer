"""Verify short probes use real public targets and official fiber classification."""
import io
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).parent/'project'))
from probe import Probe
from capture import Capture
from challenge.v4_workflow import V4Workflow
from challenge.v4_fiber_map import FiberGrid
from datetime import datetime,timezone


class ProbeTests(unittest.TestCase):
    def test_geometry_all_four_cards(self):
        sampled=0
        for card in 'abcd':
            workflow=V4Workflow(ROOT/'evaluation_assets/formal-v4-observed-timeline-v4/cards'/card)
            probe=Probe(workflow.initialize_payload(900,1))
            grid=FiberGrid.from_config(workflow.scenario.fiber_config)
            for hour in (0,2,4):
                now=probe.nights[0][0]+hour*3600
                for az in (0,90,180,270):
                    action,note=probe.plan(now,az,55,'test',1,probe.minimum)
                    if action is None:
                        self.assertIn('coverage',note);continue
                    sampled+=1
                    self.assertLessEqual(len(action['assignments']),4)
                    self.assertEqual(action['duration_seconds'],probe.minimum)
                    for cell,tid in action['assignments'].items():
                        target=probe.targets[tid]
                        result=grid.classify_target(target['ra'],target['dec'],datetime.fromtimestamp(now,timezone.utc),action['pointing']['alt_deg'],action['pointing']['az_deg'],probe.site.lat,probe.site.lon,0,0)
                        self.assertEqual(result.region,'glass')
                        self.assertEqual(result.fiber_id,int(cell))
        self.assertGreater(sampled,12)

    def test_final_wait_receives_tail(self):
        workflow=V4Workflow(ROOT/'evaluation_assets/formal-v4-observed-timeline-v4/cards/a')
        probe=Probe(workflow.initialize_payload(900,1))
        self.assertEqual(probe.wait(probe.end-900)['until_utc'],__import__('astro').format_utc(probe.end-1))
        self.assertEqual(probe.wait(probe.end-1)['duration_seconds'],probe.minimum)

    def test_capture_does_not_mutate_input_and_keeps_probe_context(self):
        raw=json.dumps({'message_type':'decision_request','payload':{'new_messages':[{'reason':'灯说有雨，立希更正'}],'wallclock':{}}})+'\n'
        log=io.StringIO();capture=Capture(log);capture.record(raw,{'pair_id':1,'coverage':'sampled'});capture.flush()
        import base64,zlib
        packet=json.loads(log.getvalue().split(' ',1)[1]);entry=json.loads(zlib.decompress(base64.b64decode(packet['data'])))
        self.assertEqual(entry['annotations']['pair_id'],1)
        self.assertEqual(entry['message']['payload']['new_messages'][0]['reason'],'灯说有雨，立希更正')
        self.assertIn('wallclock',json.loads(raw)['payload'])

if __name__=='__main__':unittest.main()
