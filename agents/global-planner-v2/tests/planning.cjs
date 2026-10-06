const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

test('missing model runtime fails instead of silently emitting wait actions', () => {
  const root = path.resolve(__dirname, '..');
  const request = JSON.parse(fs.readFileSync(path.join(root, 'models/example-reliability-request.json'), 'utf8'));
  request.initialize.targets = {
    columns:['target_id','ra_deg','dec_deg','target_class','feature_flux','science_weight','required'],
    rows:[['test-target',10,-20,'LRG',1,1,false]],
  };
  request.initialize.footprint = [];
  const messages = [
    {protocol_version:'participant-agent-protocol-v4',message_type:'initialize',payload:request.initialize},
    {protocol_version:'participant-agent-protocol-v4',message_type:'decision_request',decision_sequence:1,
      payload:{now_utc:request.now_utc,running_total:0,new_messages:[],active_requests:[],last_result:null}},
  ];
  const result = spawnSync(process.execPath, [path.join(root, 'dist/agent.js')], {
    input: messages.map(m => JSON.stringify(m)).join('\n')+'\n', encoding:'utf8', maxBuffer:Infinity,
    env:{...process.env,BASE_MODEL_PYTHON:path.join(root,'missing-runtime-for-test'),BASE_CONDITIONS_MODELS:'reliability',BASE_FAULT_MODEL:'on'},
  });
  assert.equal(result.status,1,result.stderr);
  assert.equal(result.stdout,'');
  assert.match(result.stderr,/Model Python missing/);
});

test('autonomous duration preserves the selected exposure under suspected faults', async () => {
  const root = path.resolve(__dirname, '../../..');
  const python = path.join(root, 'research/observing_conditions/.venv/bin/python');
  const script = `from pathlib import Path
from tempfile import TemporaryDirectory
import json
from research.global_planning.data import build_micro,micro_entries
from challenge.v4_workflow import V4Workflow
with TemporaryDirectory() as d:
 b=build_micro(micro_entries()[0],Path(d)/'bundle')
 print(json.dumps(V4Workflow(b).initialize_payload(900,1)))`;
  const result=spawnSync(python,['-c',script],{cwd:root,encoding:'utf8',maxBuffer:Infinity});
  assert.equal(result.status,0,result.stderr);
  const initial=JSON.parse(result.stdout);
  initial.instrument.max_duration_seconds=300;
  process.env.BASE_CONDITIONS_MODELS='off';process.env.BASE_FAULT_MODEL='off';
  const {Agent}=require('../dist/agent.js');
  const agent=new Agent(initial);
  try {
    const t=agent.sv.nights[0][0];
    const plan=agent.planner.plan(t,0,14,60,240,true);
    assert.ok(plan,'self-generated public scene must have an observable plan');
    plan.duration=180;plan.picks=agent.planner._picks(plan,t,240,true);
    agent.sv.sky.suspect=true;
    const legacy=await agent._action(plan,t,0,240,true);
    const selected=await agent._action({...plan,fixedDuration:true},t,0,240,true);
    assert.equal(legacy.duration_seconds,144);
    assert.equal(selected.duration_seconds,180);
  } finally {await agent.close();}
});
