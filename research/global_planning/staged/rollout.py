from __future__ import annotations
import gzip, io, json, math, os, resource, sqlite3, subprocess, sys, time
from pathlib import Path
import numpy as np
from ..common import ROOT,save,digest
from ..environment import OfficialEnvironment,rewards_from_episode,returns_to_go
from ..bridge import DEFAULT_NODE
from .features import encode
AGENT=ROOT/'agents/global-planner-v2'

class Environment(OfficialEnvironment):
    def close(self):
        if self.process is not None:
            self.process.stdin.close()
            # Drain pending public messages before wait; large pipe writes otherwise
            # prevent a disconnected engine from exiting. This is not a timeout.
            for _ in self.process.stdout: pass
            self.process.wait();self.process.stdout.close();self.log.close();self.process=None


def array_blob(a):
    file=io.BytesIO();np.save(file,a,allow_pickle=False);return file.getvalue()
def array_load(blob):return np.load(io.BytesIO(blob),allow_pickle=False)

class Trajectory:
    def __init__(self,folder):self.folder=Path(folder);self.summary=json.loads((self.folder/'episode.json').read_text())
    def rows(self):
        db=sqlite3.connect(f'file:{self.folder/"choices.sqlite"}?mode=ro',uri=True)
        try:
            for data in db.execute('SELECT step,x,c,selected,teacher,logp,value,return,kind FROM choices ORDER BY id'):
                step,x,c,selected,teacher,logp,value,ret,kind=data
                yield dict(step=step,x=array_load(x),c=array_load(c),selected=selected,teacher=teacher,
                           log_probability=logp,value=value,return_=ret,kind=kind)
        finally:db.close()

class Bridge:
    def __init__(self,payload,output,stage,node=None,models=True,rule_only=False):
        self.log=Path(output).open('w');executable=str(node or os.getenv('GLOBAL_PLANNER_NODE',DEFAULT_NODE))
        version=subprocess.check_output([executable,'--version'],text=True).strip()
        if not version.startswith('v22.'):raise ValueError('Node 22 required')
        env=dict(os.environ,BASE_MODEL_PYTHON=sys.executable,BASE_CONDITIONS_MODELS='reliability' if models else 'off',BASE_FAULT_MODEL='on' if models else 'off')
        self.process=subprocess.Popen([executable,'dist/tools/planning-bridge.js'],cwd=AGENT,env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1)
        self.send(dict(operation='initialize',payload=payload,stage=stage,rule_only=rule_only))
        if self.read()['type']!='ready':raise RuntimeError('Bridge initialization failed')
    def send(self,v):self.process.stdin.write(json.dumps(v,allow_nan=False)+'\n');self.process.stdin.flush()
    def read(self):
        line=self.process.stdout.readline()
        if not line:raise RuntimeError(f'Bridge exited {self.process.wait()}; {self.log.name}')
        return json.loads(line)
    def close(self):
        if self.process.poll() is None:self.send(dict(operation='close'))
        self.process.stdin.close()
        for _ in self.process.stdout: pass
        self.process.wait();self.process.stdout.close();self.log.close()

def rule(frame):return frame['teacher'],dict(trainable=True)
def expanded_rule(frame):
    if frame['kind']=='region':
        index=max(range(len(frame['candidates'])),key=lambda i:frame['candidates'][i]['features']['nv']+(frame['candidates'][i]['features']['urgency']-1)*frame['candidates'][i]['features']['expected_science'])
    else:index=frame['teacher']
    return index,dict(trainable=True)

def episode(bundle,folder,policy=rule,*,stage=2,key='rule',node=None,models=True,rule_only=False):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    identity=dict(bundle_source=digest(Path(bundle)/'source.json') if (Path(bundle)/'source.json').exists() else str(bundle),
                  policy=key,stage=stage,models=models,rule_only=rule_only,compiled_bridge=digest(AGENT/'dist/tools/planning-bridge.js'),
                  compiled_agent=digest(AGENT/'dist/agent.js'),compiled_planner=digest(AGENT/'dist/core/planner.js'),compiled_planning=digest(AGENT/'dist/planning.js'),features=digest(Path(__file__).parent/'features.py'))
    if (folder/'episode.json').exists():
        existing=json.loads((folder/'episode.json').read_text())
        if existing['identity']!=identity:
            compatibility=folder/'resume-compatibility.json'
            if stage!=2 or not compatibility.exists():raise ValueError('Episode source/config mismatch')
            approved=json.loads(compatibility.read_text())
            if approved['original_identity_sha256']!=digest(existing['identity']) or approved['new_identity_sha256']!=digest(identity):raise ValueError('Resume compatibility does not match exact identities')
        return Trajectory(folder)
    start=time.perf_counter();env=Environment(bundle,folder);bridge=None
    database=folder/'choices.sqlite'
    if database.exists():database.unlink()
    db=sqlite3.connect(database);db.execute('CREATE TABLE choices (id INTEGER PRIMARY KEY,step INTEGER,x BLOB,c BLOB,selected INTEGER,teacher INTEGER,logp REAL,value REAL,return REAL,kind TEXT)')
    snapshots=[];choices=steps=0;feature_seconds=inference_seconds=0.;last_selected=None
    trace=gzip.open(folder/'trace.jsonl.gz','wt');peak_node=0
    try:
        initialize,event=env.reset();table=initialize['targets'];column=table['columns'].index('science_weight');catalogue_weight=sum(float(r[column]) for r in table['rows']);trace.write(json.dumps(dict(type='initialize',payload=initialize))+'\n')
        bridge=Bridge(initialize,folder/'agent.log',stage,node,models,rule_only)
        while event['type']!='done':
            payload=event['payload'];snapshots.append(dict(running_total=payload['running_total']))
            trace.write(json.dumps(event)+'\n');bridge.send(dict(operation='decide',payload=payload));response=bridge.read()
            last_selected=None
            while response['type']=='proposal':
                frame=response['frame'];frame['global']['catalogue_weight']=catalogue_weight;before=time.perf_counter();x,c=encode(frame);feature_seconds+=time.perf_counter()-before
                before=time.perf_counter();selected,details=policy(frame);inference_seconds+=time.perf_counter()-before
                if not isinstance(selected,int) or not 0<=selected<len(frame['candidates']):raise ValueError('Invalid selected index')
                last_selected=frame['candidates'][selected]['action']
                trace.write(json.dumps(dict(type='proposal',frame=frame,selected=selected,details=details))+'\n')
                if details.get('trainable',True):
                    db.execute('INSERT INTO choices VALUES (?,?,?,?,?,?,?,?,?,?)',(choices,steps,array_blob(x),array_blob(c),selected,frame['teacher'],details.get('log_probability'),details.get('value'),None,frame['kind']));choices+=1
                bridge.send(dict(operation='choose',index=selected));response=bridge.read()
            if response['type']!='action':raise RuntimeError('Missing action')
            if last_selected is not None and response['action']!=last_selected:raise AssertionError('Selected action mismatch')
            peak_node=max(peak_node,response.get('rss',0));trace.write(json.dumps(response)+'\n')
            event=env.step(response['action']);steps+=1
            db.commit();trace.flush()
            if steps%100==0:save(folder/'progress.json',dict(steps=steps,choices=choices,now_utc=payload['now_utc'],wall_seconds=time.perf_counter()-start))
        report=event['report'];rewards=rewards_from_episode(snapshots,report);returns=returns_to_go(rewards)
        db.executemany('UPDATE choices SET return=? WHERE step=?',[(v,i) for i,v in enumerate(returns)]);db.commit()
        trace.write(json.dumps(event)+'\n');trace.close();db.close()
        summary=dict(identity=identity,total=report['total'],components=report['components'],termination=report['termination'],
            choices=choices,steps=steps,rewards=rewards,reward_sum=math.fsum(rewards),wall_seconds=time.perf_counter()-start,
            engine=event['timing'],feature_seconds=feature_seconds,policy_seconds=inference_seconds,node_cpu=response.get('cpu'),node_peak_rss=peak_node,
            trainer_peak_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
            report_counts=report.get('counts',{}),models_enabled=models)
        save(folder/'episode.json',summary);return Trajectory(folder)
    except BaseException as error:
        save(folder/'error.json',dict(status='failed',error=repr(error),steps=steps));raise
    finally:
        trace.close()
        try:db.close()
        except:pass
        if bridge:bridge.close()
        env.close()
