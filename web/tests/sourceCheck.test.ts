import test from 'node:test'
import assert from 'node:assert/strict'
import { inspectSourceNames, zipEntryNames } from '../src/lib/sourceCheck.ts'

test('a ZIP with only dot files and examples has no code; a manifest or a source file counts', () => {
  assert.deepEqual(inspectSourceNames(['proj/', 'proj/.gitignore', 'proj/agent/.env.example', 'proj/.venv/x.py']),
    { manifest: false, code: false, files: ['proj/.gitignore', 'proj/agent/.env.example', 'proj/.venv/x.py'] })
  assert.equal(inspectSourceNames(['a/agent.py']).code, true)
  assert.equal(inspectSourceNames(['a/Cargo.toml']).code, true)
  assert.equal(inspectSourceNames(['__MACOSX/a.py', 'a/README.md']).code, false)
  assert.equal(inspectSourceNames(['a/observer.project.json']).manifest, true)
})

test('central directory names are read from a stored ZIP', () => {
  // Minimal ZIP with one empty stored entry "a.py".
  const name = new TextEncoder().encode('a.py')
  const local = new Uint8Array(30 + name.length), central = new Uint8Array(46 + name.length), end = new Uint8Array(22)
  const lv = new DataView(local.buffer), cv = new DataView(central.buffer), ev = new DataView(end.buffer)
  lv.setUint32(0, 0x04034b50, true); lv.setUint16(26, name.length, true); local.set(name, 30)
  cv.setUint32(0, 0x02014b50, true); cv.setUint16(28, name.length, true); central.set(name, 46)
  ev.setUint32(0, 0x06054b50, true); ev.setUint16(8, 1, true); ev.setUint16(10, 1, true)
  ev.setUint32(12, central.length, true); ev.setUint32(16, local.length, true)
  const zip = new Uint8Array([...local, ...central, ...end])
  assert.deepEqual(zipEntryNames(zip.buffer), ['a.py'])
  assert.equal(zipEntryNames(new Uint8Array([1, 2, 3]).buffer), null)
})
