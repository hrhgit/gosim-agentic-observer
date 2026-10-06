import assert from 'node:assert/strict'
import test from 'node:test'
import { canPrepareAgain, cardFolderName, flattenResultEntries, formatDailyReset, formatDateTime, manifestForDisplay, orderedCardFolder, revisionErrorText } from '../src/lib/projectText.ts'
import { bytes, githubHandle } from '../src/lib/format.ts'

test('dates follow the page language instead of the browser default', () => {
  const value = '2026-09-26T11:00:37Z'
  assert.equal(formatDateTime(value, 'zh', 'Asia/Shanghai'), '2026/09/26 19:00')
  assert.match(formatDateTime(value, 'en', 'Asia/Shanghai'), /^09\/26\/2026,? 19:00$/)
  assert.ok(!formatDateTime(value, 'zh', 'UTC').includes('PM'))
  assert.equal(formatDateTime(null, 'zh'), '—')
  assert.equal(formatDateTime('not a date', 'en'), '—')
})

test('the daily reset names its time zone', () => {
  assert.equal(formatDailyReset('2026-09-27T00:00:00Z', 'zh'), '每天 UTC 0:00（北京时间 8:00）重置')
  assert.equal(formatDailyReset('2026-09-27T00:00:00Z', 'en'), 'Resets daily at 0:00 UTC (8:00 Beijing time)')
  assert.equal(formatDailyReset(undefined, 'zh'), '每天 UTC 0:00（北京时间 8:00）重置')
  assert.equal(formatDailyReset('2026-09-27T20:30:00Z', 'zh'), '每天 UTC 20:30（北京时间 4:30）重置')
})

test('preparation errors are shown in Chinese on the zh page', () => {
  assert.equal(revisionErrorText('Project preparation failed. Please retry.', 'zh'), '项目准备失败，请重新提交。')
  assert.equal(revisionErrorText('Project preparation failed: Automatic adaptation could not identify the entry point.', 'zh'),
    '项目准备失败：Automatic adaptation could not identify the entry point.')
  assert.equal(revisionErrorText('Public test failed. Check the project interface and submit again.', 'zh'), '公开场景测试未通过，请检查项目接口后重新上传。')
  assert.equal(revisionErrorText('Project preparation failed. Please retry.', 'en'), 'Project preparation failed. Please retry.')
  assert.equal(revisionErrorText('Something else', 'zh'), 'Something else')
  assert.equal(revisionErrorText(null, 'zh'), '')
})

test('fixed preparation reasons from the runtime are translated, others stay as written', () => {
  const failed = (reason: string) => revisionErrorText('Project preparation failed: ' + reason, 'zh')
  assert.equal(failed('The model provider rejected the request (HTTP 404). Check the API endpoint, model name, key and balance on the Participate page.'),
    '项目准备失败：模型服务商拒绝了请求（HTTP 404）。请在「参赛」页检查 API 地址、模型名、密钥和余额。')
  assert.equal(failed('The model provider rejected the request. Check the API endpoint, model name, key and balance on the Participate page.'),
    '项目准备失败：模型服务商拒绝了请求。请在「参赛」页检查 API 地址、模型名、密钥和余额。')
  assert.match(failed('No model API is set up for your team. Set one under Model API on the Participate page, or add observer.project.json so no automatic adaptation is needed.'),
    /^项目准备失败：本队还没有设置模型 API。/)
  assert.match(failed('No open Participate page answered the model request. Your team does not save its model key, so keep the Participate page open with the model API connected while the project is prepared, or save the key there, or add observer.project.json so no automatic adaptation is needed.'),
    /保持「参赛」页面打开/)
  assert.match(failed('Your model API did not answer within 140 seconds. Use a faster model or endpoint, or add observer.project.json so no automatic adaptation is needed.'),
    /在 140 秒内没有响应。可换用更快的模型/)
  assert.match(failed('The adaptation model did not return a valid interface proposal.'), /没有返回有效的接口方案/)
  assert.equal(failed('Model call failed (HTTP 503).'), '项目准备失败：模型调用失败（HTTP 503）。请稍后重新提交。')
  assert.equal(failed('build must be a nonempty array of command arguments.'), '项目准备失败：build must be a nonempty array of command arguments.')
  const english = 'Project preparation failed: Model service is unavailable.'
  assert.equal(revisionErrorText(english, 'en'), english)
})

test('only failed public repositories can be prepared again from the same source', () => {
  const repo = { status: 'failed', source_kind: 'repository', source_location: 'https://github.com/team/project' }
  assert.ok(canPrepareAgain(repo))
  assert.ok(!canPrepareAgain({ ...repo, status: 'reviewable' }))
  assert.ok(!canPrepareAgain({ ...repo, archived_at: '2026-09-27T00:00:00Z' }))
  assert.ok(!canPrepareAgain({ status: 'failed', source_kind: 'zip', source_location: 'uploads/x/source.zip' }))
})

test('a combined-results ZIP folder name is ascii-safe and never collides with an unknown slug', () => {
  assert.equal(cardFolderName('v4-practice-alpha', 'run-1'), 'practice-alpha')
  assert.equal(cardFolderName('v4-a', 'run-1'), 'a')
  assert.equal(cardFolderName(null, 'run-1'), 'run-1')
  assert.equal(cardFolderName(undefined, 'run-1'), 'run-1')
  assert.equal(cardFolderName('', 'run-1'), 'run-1')
  assert.equal(cardFolderName('练习卡 α', 'run-1'), 'run-1')
  assert.equal(cardFolderName('scenario/with slashes', 'run-1'), 'scenario-with-slashes')
})

test('the combined results ZIP keeps card order and drops the runner repository folder', () => {
  assert.deepEqual(['practice-alpha', 'practice-beta', 'practice-gamma', 'practice-delta'].map((f, i) => orderedCardFolder(i, 4, f)),
    ['1-practice-alpha', '2-practice-beta', '3-practice-gamma', '4-practice-delta'])
  assert.equal(orderedCardFolder(0, 12, 'a'), '01-a')
  const wrapped = { 'org-runner-abc123/': 'dir', 'org-runner-abc123/decisions.csv': 'csv', 'org-runner-abc123/replay/frames.json': 'frames', 'agent.log': 'log' }
  assert.deepEqual(flattenResultEntries(wrapped), { 'decisions.csv': 'csv', 'replay/frames.json': 'frames', 'agent.log': 'log' })
  assert.deepEqual(flattenResultEntries({ 'org-runner-abc123/decisions.csv': 'csv', 'org-runner-abc123/agent.log': 'log' }),
    { 'decisions.csv': 'csv', 'agent.log': 'log' })
  // Already flat, or several top-level entries: unchanged.
  const flat = { 'decisions.csv': 'csv', 'replay/frames.json': 'frames', 'agent.log': 'log' }
  assert.deepEqual(flattenResultEntries(flat), flat)
  const two = { 'a/x.csv': 'x', 'b/y.csv': 'y' }
  assert.deepEqual(flattenResultEntries(two), two)
})

test('the review hides the internally normalised interface version', () => {
  const manifest = { schema_version: 1, protocol: 'jsonl-v2', run: ['python', 'agent.py'] }
  assert.deepEqual(manifestForDisplay(manifest), { schema_version: 1, run: ['python', 'agent.py'] })
  assert.equal(JSON.stringify(manifestForDisplay(manifest)).includes('jsonl'), false)
  assert.equal(manifest.protocol, 'jsonl-v2')
  assert.equal(manifestForDisplay(null), null)
})

test('file sizes use readable units', () => {
  assert.equal(bytes(512), '512 B')
  assert.equal(bytes(44 * 1024), '44.0 KB')
  assert.equal(bytes(3 * 1024 * 1024), '3.0 MB')
})

test('only valid GitHub usernames are used for avatars', () => {
  for (const [input, handle] of [['octocat', 'octocat'], ['@octo-cat', 'octo-cat'], ['https://github.com/octocat/repo', 'octocat'], ['  octocat ', 'octocat']]) {
    assert.equal(githubHandle(input), handle)
  }
  for (const input of ['张三', 'John Smith', '-bad', 'a'.repeat(40), 'name_with_underscore', '', null, undefined]) {
    assert.equal(githubHandle(input), '')
  }
})
