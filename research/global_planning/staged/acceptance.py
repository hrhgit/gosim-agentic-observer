from __future__ import annotations
import json, os, shlex, shutil, subprocess, sys, time, zipfile
from pathlib import Path
from ..common import ROOT,ENGINE,save,digest,verify_engine
from .rollout import AGENT
from .export_runtime import materialize
from .entry import export

def package(config,output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    files=[]
    for sub in ('src','models','runtime','tests'):
        for path in (AGENT/sub).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:files.append(path)
    files.extend(AGENT/f for f in ('package.json','package-lock.json','tsconfig.json','NOTICE','observer.project.json'))
    archive=output/'global-planner-v2.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in files:z.write(path,str(path.relative_to(AGENT)))
    report=dict(archive=str(archive),zip_bytes=archive.stat().st_size,unpacked_bytes=sum(p.stat().st_size for p in files),files=len(files),
                passed=archive.stat().st_size<=50*1024**2 and sum(p.stat().st_size for p in files)<=100*1024**2 and len(files)<=10000,
                dependencies='Installed in build; .runtime, node_modules, research traces excluded',selected_config=digest(Path(config)))
    save(output/'package.json',report);return report

def linux_check(bundle,config,output,*,full_schedule=False):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    materialize()
    # Mounted inference source overlays the already provisioned CPU runtime image.
    mounts=[f'{AGENT}/dist:/workspace/dist:ro',f'{AGENT}/runtime:/workspace/runtime:ro',
            f'{Path(config).parent.resolve()}:/workspace/models/global-planning:rw',f'{ENGINE}:/workspace/official-engine:ro',
            f'{Path(bundle).resolve()}:/workspace/test-card:ro',f'{output.resolve()}:/workspace/check-output:rw']
    cmd=['docker','run','--rm','--platform','linux/amd64','--cpus','2','--memory','2g','--pids-limit','128','--read-only',
         '--tmpfs','/tmp:rw,size=256m']
    for mount in mounts:cmd+=['-v',mount]
    cmd+=['gosim-global-planning-v2','/workspace/.runtime/venv/bin/python','-B','/workspace/official-engine/run_local.py',
          '--card','/workspace/test-card','--agent','node dist/global-entry.js','--agent-cwd','/workspace',
          '--out','/workspace/check-output/run','--quiet']
    before=time.perf_counter()
    with (output/'container.log').open('w') as stream:result=subprocess.run(cmd,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    report_path=output/'run/score_report.json';report=json.loads(report_path.read_text()) if report_path.exists() else None
    evidence=dict(command=cmd,exit_code=result.returncode,wall_seconds=time.perf_counter()-before,
                 engine_manifest=verify_engine(),report=report,system='Linux x86_64 under local emulation',
                 passed=result.returncode==0 and report is not None and report['termination']['reason']=='survey_complete',
                 limits=dict(cpu=2,memory_bytes=2*1024**3,pids=128,read_only_root=True,tmp_bytes=256*1024**2),
                 full_schedule=full_schedule,note='Full formal schedule under local emulation; not online timing' if full_schedule else 'Micro interface/resources acceptance is not formal-card Linux speed or tournament evidence')
    save(output/'acceptance.json',evidence);return evidence


def native_check(bundle,config,output,*,node=None,full_schedule=False):
    """Actual competition entry on the local host, as requested by the user."""
    from ..bridge import DEFAULT_NODE
    output=Path(output);output.mkdir(parents=True,exist_ok=True);materialize()
    executable=str(node or os.environ.get('GLOBAL_PLANNER_NODE',DEFAULT_NODE))
    # Official runner intentionally sanitizes inherited environment. Pass the
    # explicit local runtime/config through the authorized entry command.
    agent_command=shlex.join(['/usr/bin/env',f'BASE_MODEL_PYTHON={sys.executable}',
        f'GLOBAL_PLANNER_CONFIG={Path(config).resolve()}',executable,str(AGENT/'dist/global-entry.js')])
    cmd=[sys.executable,str(ENGINE/'run_local.py'),'--card',str(Path(bundle).resolve()),
         '--agent',agent_command,'--agent-cwd',str(AGENT),'--out',str(output.resolve()/'run'),'--quiet']
    env=dict(os.environ,BASE_MODEL_PYTHON=sys.executable,GLOBAL_PLANNER_CONFIG=str(Path(config).resolve()),GLOBAL_PLANNER_NODE=executable)
    before=time.perf_counter()
    with (output/'entry.log').open('w') as stream:
        result=subprocess.run(cmd,cwd=ROOT,env=env,stdout=stream,stderr=subprocess.STDOUT)
    path=output/'run/score_report.json';report=json.loads(path.read_text()) if path.exists() else None
    evidence=dict(command=cmd,exit_code=result.returncode,wall_seconds=time.perf_counter()-before,
       engine_manifest=verify_engine(),report=report,system=sys.platform,full_schedule=full_schedule,
       config_sha256=digest(Path(config)),policy_sha256=json.loads(Path(config).read_text())['policy_sha256'],
       container_requested=False,passed=result.returncode==0 and report is not None and report['termination']['reason']=='survey_complete',
       scope='Native complete entry only; user explicitly removed container testing')
    save(output/'acceptance.json',evidence);return evidence
