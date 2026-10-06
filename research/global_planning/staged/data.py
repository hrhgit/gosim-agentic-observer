from __future__ import annotations
import copy, importlib.util, json, os, shutil, subprocess, sys
from pathlib import Path
from ..common import ROOT, digest, save

def entries():
    return [dict(id=f'full-{split}-{card}-{i}',split=split,card=card,seed=8500000+si*10000+ci*100+i,
                 source='Formal unchanged public inputs + SELF-GENERATED hidden conditions')
            for si,(split,n) in enumerate([('train',4),('validation',2),('test',4),('stress',2)])
            for ci,card in enumerate('abcd') for i in range(n)]

def build(entry, folder):
    folder=Path(folder); meta=folder/'source.json'
    if meta.exists():
        record=json.loads(meta.read_text())
        if record['entry']!=entry: raise ValueError('Scenario identity changed')
        for file,sha in record['hashes'].items():
            if digest(folder/file)!=sha: raise ValueError(f'Scenario changed: {file}')
        return folder
    folder.parent.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,'-m','research.global_planning.staged.data','--entry',json.dumps(entry),'--folder',str(folder)]
    subprocess.run(command,cwd=ROOT,check=True)
    return folder

def generate(entry,folder):
    spec=importlib.util.spec_from_file_location('formal_generator',ROOT/'scripts/build-formal-self-generated-evaluations.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    profile=copy.deepcopy(module.PROFILES[entry['card']]);profile['seed']=entry['seed']
    if entry['split']=='stress':
        profile['background_closure']['reopen_probability_per_closed_slot']*=.45
        profile['requests']['count']*=2
        profile['requests']['window_nights']=max(1,profile['requests']['window_nights']-1)
        for definition in profile.get('condition_overrides',{}).values():
            if 'duration_slots' in definition:definition['duration_slots']=[int(x*2) for x in definition['duration_slots']]
    temporary=folder.parent/(folder.name+'.generating')
    if temporary.exists(): shutil.rmtree(temporary)
    module._build_one(temporary,entry['card'],profile=profile,suite_name='global-planning-v2')
    bundle=temporary/'cards'/entry['card']
    # Move generated announcements away from original public files. The scenario
    # routes publication streams to the explicitly synthetic overlay directory.
    overlay=bundle/'generated_public';overlay.mkdir()
    cfg=json.loads((bundle/'config/v4_scenario.json').read_text())
    for name,key in [('v4_bulletins.jsonl','bulletins_jsonl'),('v4_forecasts.jsonl','forecasts_jsonl')]:
        (bundle/'public'/name).rename(overlay/name)
        original=ROOT/'cards'/entry['card']/'public'/name
        if original.exists():shutil.copyfile(original,bundle/'public'/name)
        cfg['products'][key]='../generated_public/'+name
    save(bundle/'config/v4_scenario.json',cfg)
    original_hashes={}
    for file in (ROOT/'cards'/entry['card']).rglob('*'):
        if file.is_file():
            rel=file.relative_to(ROOT/'cards'/entry['card'])
            original_hashes[str(rel)]=digest(file)
            if str(rel).startswith('public/') and (bundle/rel).exists(): assert digest(bundle/rel)==digest(file)
    save(bundle/'source.json',dict(entry=entry,official_hidden_data=False,full_calendar=True,
        original_public_hashes=original_hashes,generator_sha256=digest(ROOT/'scripts/build-formal-self-generated-evaluations.py'),
        hashes={str(p.relative_to(bundle)):digest(p) for p in bundle.rglob('*') if p.is_file()},
        publication_overlay='generated_public is SELF-GENERATED; original formal public data remains unchanged'))
    bundle.rename(folder);shutil.rmtree(temporary)

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--entry');p.add_argument('--folder',type=Path);a=p.parse_args()
    generate(json.loads(a.entry),a.folder)
