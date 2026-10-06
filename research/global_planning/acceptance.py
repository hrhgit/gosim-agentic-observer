"""Official-runtime acceptance is separate from unrestricted training throughput."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

from .bridge import DEFAULT_NODE
from .common import ROOT, AGENT, ENGINE, save, digest, verify_engine


def official_run(card, output, *, node=None):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    command=shlex.join(['/usr/bin/env',f'BASE_MODEL_PYTHON={ROOT / "research/observing_conditions/.venv/bin/python"}',
                        str(node or DEFAULT_NODE),'dist/agent.js'])
    identity=dict(card=str(Path(card).resolve()),engine_manifest=verify_engine(),
                  entrypoint=digest(AGENT/'dist/agent.js'),command=command)
    result_path=output/'acceptance.json'
    if result_path.exists():
        result=json.loads(result_path.read_text())
        if result['identity']!=identity:raise ValueError('Official-run identity changed')
        return result
    report_path=output/'score_report.json'
    if not report_path.exists():
        with (output/'runner.stdout.json').open('w') as stdout,(output/'runner.stderr.log').open('w') as stderr:
            process=subprocess.run([sys.executable,str(ENGINE/'run_local.py'),'--card',str(card),
                '--agent',command,'--agent-cwd',str(AGENT),'--out',str(output),'--quiet'],cwd=ROOT,stdout=stdout,stderr=stderr)
        if process.returncode:raise RuntimeError('Official runner failed; inspect logs')
    report=json.loads(report_path.read_text())
    workflow=json.loads((output/'workflow_result.json').read_text())
    log=(output/'agent.log').read_text()
    failures=[s for s in ('decide failed:','initialize failed:','Model Python missing','Traceback (most recent call last)') if s in log]
    passed=report['termination']['reason']=='survey_complete' and report['counts']['observe_actions']>0 and not failures
    result=dict(identity=identity,passed=passed,score=report['total'],counts=report['counts'],
                fair_clock=workflow['fair_clock'],failures=failures,
                local_models_called='SURVEY26_MODELS_V1' in log and 'SURVEY26_FAULT_MODEL_V1' in log,
                note='macOS local official runner; not a Linux server speed measurement')
    result['passed']=bool(passed and result['local_models_called'])
    save(result_path,result)
    if not result['passed']:raise RuntimeError('Completed runner is not sufficient: acceptance failed')
    return result
