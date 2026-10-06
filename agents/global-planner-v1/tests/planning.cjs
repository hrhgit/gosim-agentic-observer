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
