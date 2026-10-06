const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createInterface } = require('node:readline');
const { spawnSync } = require('node:child_process');
const { BaselineModels, ConditionsSession, ModelRegistry, prepareConditions } = require('../dist/models');
const root = path.resolve(__dirname, '..');
const request = () => JSON.parse(fs.readFileSync(path.join(root, 'models/example-request.json'), 'utf8'));

test('both frozen models keep every candidate and honest evidence', () => {
  const models = new BaselineModels(), input = request();
  assert.ok(models.registry.list().every(m => !m.loaded));
  const results = models.predictConditions(['conditions-gru', 'conditions-mlp'], input);
  assert.ok(models.registry.list().every(m => m.loaded));
  for (const result of results) {
    assert.equal(result.predictions.length, input.candidates.length);
    assert.equal(result.automatic_actions, false);
    assert.equal(result.joint_success_probability, null);
    assert.deepEqual(result.predictions.map(r => r.candidate_id), input.candidates.map(r => r.candidate_id));
    for (const row of result.predictions.filter(r => r.eligible)) {
      assert.ok(row.q05 <= row.q50 && row.q50 <= row.q95);
      assert.ok(0 <= row.u05 && row.u05 <= row.u50 && row.u50 <= row.u95 && row.u95 <= 1);
      assert.ok(Math.abs(row.p_dark + row.p_bright + row.p_backup - 1) < 1e-6);
    }
  }
  assert.equal(results[0].final_test_passed, false);
  assert.equal(results[1].final_test_passed, null);
  assert.equal(results[1].validation_reliable, false);
});

test('future messages cannot change outputs; out-of-order updates match fresh sessions', () => {
  const models = new BaselineModels(), input = request();
  const before = models.predictConditions(['conditions-gru', 'conditions-mlp'], input);
  const future = structuredClone(input);
  future.bulletins.push({ issued_at_utc: new Date(Date.parse(input.now_utc) + 1000).toISOString(),
    notices: [{ event_kind: 'storm', direction: 'ALL' }] });
  future.forecasts.push({ issued_at_utc: new Date(Date.parse(input.now_utc) + 1000).toISOString(),
    coverage_start_utc: input.now_utc, coverage_end_utc: new Date(Date.parse(input.now_utc) + 86400000).toISOString(),
    notices: [{ event_kind: 'rain', direction: 'ALL', nights: ['2026-11-01'] }] });
  const after = models.predictConditions(['conditions-gru', 'conditions-mlp'], future);
  assert.deepEqual(after.map(r => r.predictions), before.map(r => r.predictions));
  const ids = ['conditions-gru', 'conditions-mlp'], session = new ConditionsSession(input.initialize, models, ids);
  const bulletins = input.bulletins.map(b => ({ ...b, record_type: 'bulletin' }));
  const forecasts = input.forecasts.map(f => ({ ...f, record_type: 'forecast' }));
  session.ingest(bulletins.slice(-2)); session.ingest(bulletins.slice(0, -2).reverse());
  session.ingest(forecasts); session.ingest(bulletins);
  assert.deepEqual(session.predict(input.now_utc, input.candidates).map(r => r.predictions), before.map(r => r.predictions));
  const empty = new ConditionsSession(input.initialize, models, ids);
  const missing = { ...input, bulletins: [], forecasts: [] };
  assert.deepEqual(empty.predict(input.now_utc, input.candidates).map(r => r.predictions),
    models.predictConditions(ids, missing).map(r => r.predictions));
});

test('failed public-history updates do not leave partly applied records', () => {
  const models = new BaselineModels(), input = request();
  const session = new ConditionsSession(input.initialize, models, ['conditions-gru']);
  session.ingest(input.bulletins.map(b => ({ ...b, record_type: 'bulletin' })));
  const before = session.predict(input.now_utc, input.candidates)[0].predictions;
  const record = { record_type: 'bulletin', issued_at_utc: input.now_utc,
    notices: [{ event_kind: 'rain', direction: 'ALL' }] };
  assert.throws(() => session.ingest([record, { ...record, notices: [{ event_kind: 'unknown', direction: 'ALL' }] }]), /unsupported/);
  assert.deepEqual(session.predict(input.now_utc, input.candidates)[0].predictions, before);
  session.ingest([record]);
  assert.notDeepEqual(session.predict(input.now_utc, input.candidates)[0].predictions, before);
});

test('unknown model/task, malformed public inputs and corrupt weights fail explicitly', () => {
  const models = new BaselineModels();
  assert.throws(() => models.predictConditions(['absent'], request()), /unknown model/);
  assert.throws(() => models.registry.resolve('other-task', 'conditions-gru'), /does not implement task/);
  assert.throws(() => prepareConditions({ ...request(), weather_truth: [] }), /unsupported/);
  const input = request(); input.candidates[0].ra_deg = NaN;
  assert.throws(() => prepareConditions(input), /invalid sky coordinates/);
  const invalidLead = request(); invalidLead.candidates[0].start_utc = new Date(Date.parse(invalidLead.now_utc) + 60000).toISOString();
  assert.throws(() => prepareConditions(invalidLead), /supports starts/);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gosim-corrupt-model-'));
  try {
    fs.cpSync(path.join(root, 'models/conditions-gru'), path.join(dir, 'conditions-gru'), { recursive: true });
    const file = path.join(dir, 'conditions-gru/weights.f32'), bytes = fs.readFileSync(file); bytes[0] ^= 1; fs.writeFileSync(file, bytes);
    assert.throws(() => new BaselineModels(dir).registry.resolve('conditions', 'conditions-gru'), /checksum mismatch/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('a future model registers on another task, loads once and cannot overwrite an id', () => {
  const registry = new ModelRegistry(); let loaded = 0;
  registry.register('new-task', 'new-network', () => { loaded++; return { id: 'new-network', predict: input => ({ value: input.value + 10 }) }; });
  assert.equal(loaded, 0);
  assert.deepEqual(registry.predict('new-task', 'new-network', { value: 7 }), { value: 17 });
  assert.deepEqual(registry.predict('new-task', 'new-network', { value: 8 }), { value: 18 });
  assert.equal(loaded, 1);
  assert.throws(() => registry.register('new-task', 'new-network', () => ({})), /duplicate/);
});

test('standalone CLI preserves JSONL boundaries and exits on malformed input', () => {
  const input = request(), cli = path.join(root, 'dist/tools/predict-models.js');
  const legacyArgs = [cli, "--models", "conditions-gru,conditions-mlp"];
  const run = spawnSync(process.execPath, legacyArgs, { input: JSON.stringify(input) + '\n' + JSON.stringify(input), encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const rows = run.stdout.trim().split('\n').map(JSON.parse);
  assert.equal(rows.length, 2); assert.equal(rows[0].models.length, 2);
  const invalid = spawnSync(process.execPath, legacyArgs, { input: '{"weather_truth":42}\n', encoding: 'utf8' });
  assert.equal(invalid.status, 1); assert.equal(invalid.stdout, '');
});

async function firstObservation(card, Agent) {
  const stream = fs.createReadStream(path.join(root, `results/feedback-nominal-v3/${card}/inputs.jsonl`));
  const lines = createInterface({ input: stream }); let agent, control;
  try {
    for await (const line of lines) {
      const message = JSON.parse(line);
      if (message.message_type === 'initialize') {
        agent = new Agent(message.payload);
        const previous = process.env.BASE_CONDITIONS_MODELS, previousFault = process.env.BASE_FAULT_MODEL;
        try { process.env.BASE_CONDITIONS_MODELS = 'off'; process.env.BASE_FAULT_MODEL = 'off'; control = new Agent(message.payload); }
        finally {
          if (previousFault === undefined) delete process.env.BASE_FAULT_MODEL; else process.env.BASE_FAULT_MODEL = previousFault;
          if (previous === undefined) delete process.env.BASE_CONDITIONS_MODELS;
          else process.env.BASE_CONDITIONS_MODELS = previous;
        }
      }
      if (message.message_type === 'decision_request') {
        const action = await agent.decide(message.payload);
        const controlAction = await control.decide(message.payload);
        if (action.action === 'observe') {
          assert.deepEqual(action, controlAction);
          assert.equal(control.modelSession.latestConditions, null);
          assert.ok(control.models.registry.list().every(m => !m.loaded));
          await control.close();
          return { agent, action };
        }
      }
    }
  } finally { lines.close(); stream.destroy(); }
  throw new Error(`no observation in recorded public input: ${card}`);
}
test('real baseline decisions on formal A-D call the selected reliability and fault models', async () => {
  const { Agent } = require('../dist/agent');
  for (const card of 'abcd') {
    const { agent, action } = await firstObservation(card, Agent);
    try {
      assert.equal(action.action, 'observe', card);
      const result = agent.modelSession.latestConditions;
      assert.equal(result.model_id, 'conditions-reliability');
      assert.equal(result.predictions.length, 3 * Object.values(action.assignments).length);
      assert.ok(result.predictions.some(p => p.eligible));
      assert.equal(agent.modelSession.latestFault.model_id, 'instrument-fault-report');
      assert.equal(agent.modelSession.latestFault.report_score, null);
      assert.ok(agent.model_cpu > 0);
    } finally { await agent.close(); }
  }
});
