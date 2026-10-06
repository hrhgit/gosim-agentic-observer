"""Competition JSONL entry and checkpoint export; reads public messages only."""
from __future__ import annotations
import json, os, sys, subprocess, shutil
from pathlib import Path
import torch
from .training import policy_factory,load,Policy
from .rollout import Bridge,AGENT
from .search import FutureSampler,SearchPolicy
from ..common import save,digest,ROOT

def export(output,checkpoint,stage,upstream=None,search=None):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(checkpoint,output/'policy.pt')
    if upstream:shutil.copyfile(upstream,output/'upstream.pt')
    config=dict(stage=stage,policy='policy.pt',policy_sha256=digest(Path(checkpoint)),upstream='upstream.pt' if upstream else None,upstream_sha256=digest(Path(upstream)) if upstream else None,
                search=search,selected_for_adoption=False)
    save(output/'config.json',config)
    return output/'config.json'

def run(config):
    torch.set_num_threads(1);config=Path(config);settings=json.loads(config.read_text());base=config.parent
    if digest(base/settings['policy'])!=settings['policy_sha256']:raise ValueError('Policy checksum mismatch')
    if settings.get('upstream') and settings.get('upstream_sha256') and digest(base/settings['upstream'])!=settings['upstream_sha256']:raise ValueError('Upstream checksum mismatch')
    policy=policy_factory(base/settings['policy'],upstream=base/settings['upstream'] if settings.get('upstream') else None)
    if settings.get('search'):
        search=settings['search'];model,_,norm,_=load(base/settings['policy'])
        policy=SearchPolicy(Policy(model,norm),FutureSampler(search['sampler']),search['expansions'],search['depth'])
    bridge=None;global_weight=None
    journal=(base/'entry-choices.jsonl').open('w')
    try:
        for line in sys.stdin:
            if not line.strip():continue
            message=json.loads(line);kind=message.get('message_type')
            if kind=='initialize':
                if bridge:bridge.close()
                table=message['payload']['targets'];column=table['columns'].index('science_weight');global_weight=sum(float(r[column]) for r in table['rows'])
                bridge=Bridge(message['payload'],base/'entry-agent.log',settings['stage'],node=os.environ.get('GLOBAL_PLANNER_NODE','node'))
            elif kind=='decision_request':
                if bridge is None:raise RuntimeError('Missing initialize')
                bridge.send(dict(operation='decide',payload=message['payload']));response=bridge.read();last_selected=None
                while response['type']=='proposal':
                    frame=response['frame'];frame['global']['catalogue_weight']=global_weight
                    index,details=policy(frame);last_selected=frame['candidates'][index]['action']
                    journal.write(json.dumps(dict(type='selection',sequence=message.get('decision_sequence'),now_utc=frame['now_utc'],kind=frame['kind'],selected=index,action=last_selected,details=details))+'\n')
                    bridge.send(dict(operation='choose',index=index));response=bridge.read()
                if response['type']!='action':raise RuntimeError('Missing selected action')
                if last_selected is not None and response['action']!=last_selected:raise AssertionError('Selected/executed entry action mismatch')
                journal.write(json.dumps(dict(type='executed',sequence=message.get('decision_sequence'),action=response['action']))+'\n');journal.flush()
                print(json.dumps(dict(protocol_version=message.get('protocol_version','participant-agent-protocol-v4'),
                    message_type='decision_response',decision_sequence=message.get('decision_sequence'),**response['action'])),flush=True)
            elif kind=='finish':break
    finally:
        journal.close()
        if bridge:bridge.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();run(a.config)
