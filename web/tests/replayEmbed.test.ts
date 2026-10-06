import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import { REPLAY_LAYOUT_PATCH, REPLAY_PLAYBACK, REPLAY_POSITION, REPLAY_SEEK, cursorForRound, embedReplayHtml, isStaleReport, readPositionMessage, roundForCursor } from '../src/lib/replayEmbed.ts'

test('replay rounds and sky-map cursors address the same action', () => {
  // round k (0-based) is report action k; the map's cursor counts actions shown, so it is k + 1
  assert.equal(cursorForRound(0, 8926), 1)
  assert.equal(cursorForRound(9, 8926), 10)
  assert.equal(cursorForRound(8925, 8926), 8926)
  assert.equal(roundForCursor(1512, 8926), 1511)
  assert.equal(roundForCursor(0, 8926), 0)
  assert.equal(roundForCursor(99999, 8926), 8925)
  assert.equal(cursorForRound(99999, 8926), 8926)
  for (const k of [0, 1, 1511, 8925]) assert.equal(roundForCursor(cursorForRound(k, 8926), 8926), k)
  assert.equal(cursorForRound(3, 0), 0)
  assert.equal(roundForCursor(3, 0), 0)
})

test('only well-formed position messages are read, and reports older than the last seek are stale', () => {
  assert.equal(readPositionMessage(null), null)
  assert.equal(readPositionMessage({ type: 'other', round: 1, rounds: 2 }), null)
  assert.equal(readPositionMessage({ type: REPLAY_POSITION, round: 'x', rounds: 2 }), null)
  const msg = readPositionMessage({ type: REPLAY_POSITION, round: 4, rounds: 10, playing: true, seq: 2 })!
  assert.deepEqual(msg, { type: REPLAY_POSITION, round: 4, rounds: 10, playing: true, seq: 2 })
  assert.equal(isStaleReport(msg, 2), false)
  assert.equal(isStaleReport(msg, 3), true)
})

/** Run a miniature replay page (same globals as decision_replay.html) with the injected bridge. */
function miniReplay() {
  const page = embedReplayHtml(`<html><head><style>x{}</style></head><body><script>
const DATA={rounds:[{},{},{},{},{}]};let index=0,playing=false,speed=1,lastStep=0;
function updateUI(){painted.push(index)}
function play(){playing=true;index++;updateUI()}
function finish(){index=DATA.rounds.length-1;playing=false;document.getElementById('play').textContent='\u25B6 REPLAY';updateUI()}
</script></body></html>`)
  const scripts = [...page.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]!)
  const posted: any[] = []
  const listeners: ((e: any) => void)[] = []
  const parent = { postMessage: (data: any) => posted.push(data) }
  const button = { textContent: 'Ⅱ PAUSE' }
  const timers: (() => void)[] = []
  const select = { value: '1' }
  const ctx: any = { painted: [] as number[], parent, performance: { now: () => 0 }, setTimeout: (fn: () => void) => timers.push(fn),
    document: { getElementById: (id: string) => id === 'speed' ? select : button } }
  ctx.window = { parent, addEventListener: (_: string, fn: (e: any) => void) => listeners.push(fn) }
  vm.createContext(ctx)
  for (const s of scripts) vm.runInContext(s, ctx)
  const send = (data: any, source: any = parent) => listeners.forEach(fn => fn({ source, data }))
  return { page, ctx, posted, send, button, select, timers, get: (expr: string) => vm.runInContext(expr, ctx) }
}

test('the bridge reports each replay step and applies seeks without echoing them', () => {
  const r = miniReplay()
  assert.equal(r.posted.length, 0, 'nothing is reported on load; the page seeks first')
  r.get('play()')
  assert.deepEqual(JSON.parse(JSON.stringify(r.posted.at(-1))), { type: REPLAY_POSITION, round: 1, rounds: 5, playing: true, seq: 0 })
  r.send({ type: REPLAY_SEEK, round: 3, seq: 1 })
  assert.equal(r.get('index'), 3)
  assert.equal(r.get('playing'), false, 'a seek from the page pauses the replay')
  assert.equal(r.button.textContent, '▶ PLAY')
  assert.equal(r.posted.length, 1, 'a seek is not echoed back')
  r.send({ type: REPLAY_SEEK, round: 99, seq: 2 })
  assert.equal(r.get('index'), 4, 'seeks are clamped to the run')
  r.send({ type: REPLAY_SEEK, round: 0, seq: 3 }, {})
  assert.equal(r.get('index'), 4, 'messages from anyone but the page are ignored')
  r.get('play()')
  assert.equal(r.posted.at(-1).seq, 2, 'reports carry the last applied seek')
})

test('stored replays get the narrow-width layout the template now ships', () => {
  const template = readFileSync(new URL('../../challenge/templates/decision_replay.html', import.meta.url), 'utf8')
  const media = template.slice(template.indexOf('@media(max-width:1080px)'))
  assert.doesNotMatch(media.slice(0, media.indexOf('</style>')), /position:absolute/, 'the decision panel must not float over the sky')
  const squash = (s: string) => s.replace(/\s+/g, '')
  for (const line of REPLAY_LAYOUT_PATCH.split('\n').filter(l => l && !l.startsWith('.right'))) {
    assert.ok(squash(template).includes(squash(line)), `template lacks: ${line}`)
  }
  const page = embedReplayHtml(template)
  assert.ok(page.indexOf('data-hs26-embed>@media') < page.indexOf('</head>'))
  assert.ok(page.lastIndexOf('<script data-hs26-embed>') > page.indexOf('const DATA'))
})

test('the page can set the speed, autoplay and loop the replay', () => {
  const r = miniReplay()
  r.send({ type: REPLAY_PLAYBACK, speed: 8, play: true, loop: true })
  assert.equal(r.get('speed'), 8)
  assert.equal(r.select.value, '8')
  assert.equal(r.get('playing'), true)
  assert.equal(r.button.textContent, '\u2161 PAUSE')
  r.get('finish()')
  assert.equal(r.timers.length, 1, 'the finished run is held, then starts over')
  r.timers[0]!()
  assert.equal(r.get('index'), 0)
  assert.equal(r.get('playing'), true)
  r.send({ type: REPLAY_PLAYBACK, play: false })
  assert.equal(r.get('playing'), false)
  r.send({ type: REPLAY_PLAYBACK, loop: false })
  r.get('finish()')
  assert.equal(r.timers.length, 1, 'without loop the replay stays on its last round')
})
