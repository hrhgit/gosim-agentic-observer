import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, readdirSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import test from 'node:test'
import { unzipSync } from 'fflate'

const web = resolve(import.meta.dirname, '..')
const examplesRoot = resolve(web, '..', 'examples')
const exampleNames = readdirSync(examplesRoot, { withFileTypes: true })
  .filter((e) => e.isDirectory() && !/^[_.]/.test(e.name)).map((e) => e.name).sort()

function build(): string {
  const out = mkdtempSync(join(tmpdir(), 'example-zips-'))
  for (const script of ['build-examples.mjs', 'build-examples-bundle.mjs']) {
    execFileSync(process.execPath, [resolve(web, 'scripts', script)], { env: { ...process.env, EXAMPLES_OUT_DIR: out }, stdio: 'pipe' })
  }
  return out
}

const FORBIDDEN_PATH = /(^|\/)(\.env|\.env\.local|\.git|node_modules|target|__pycache__|\.secrets|truth)(\/|$)|\.pyc$/
// Real-looking keys only; placeholders such as "sk-..." / "<your key>" in READMEs are fine.
const SECRET = /\b(sk-[A-Za-z0-9_-]{20,}|sb_secret_[A-Za-z0-9_-]{10,}|ghp_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})/

test('every example folder gets a deterministic, clean per-example ZIP and is in the bundle', () => {
  assert.ok(['python', 'python-pro', 'typescript', 'typescript-pro', 'rust', 'rust-pro'].every((n) => exampleNames.includes(n)))
  const out = build()
  try {
    for (const name of exampleNames) {
      const bytes = readFileSync(join(out, `${name}.zip`))
      const files = unzipSync(bytes)
      const paths = Object.keys(files)
      assert.ok(paths.every((p) => p.startsWith(`${name}/`)), `${name}: single top-level folder`)
      for (const f of ['observer.project.json', 'README.md', 'AGENTS.md']) assert.ok(files[`${name}/${f}`], `${name}: has ${f}`)
      JSON.parse(Buffer.from(files[`${name}/observer.project.json`]).toString('utf8'))
      for (const p of paths) {
        assert.doesNotMatch(p, FORBIDDEN_PATH, `${name}: excluded path ${p}`)
        if (!/\.(png|jpe?g|webp|gif)$/.test(p)) assert.doesNotMatch(Buffer.from(files[p]).toString('latin1'), SECRET, `${name}: secret-looking text in ${p}`)
      }
      const code = paths.filter((p) => !p.startsWith(`${name}/docs/`))
      assert.deepEqual(code, [...code].sort(), `${name}: entries sorted`)
    }
    const bundle = Object.keys(unzipSync(readFileSync(join(out, 'gosim-observer-examples.zip'))))
    for (const name of exampleNames) assert.ok(bundle.includes(`gosim-observer-examples/${name}/observer.project.json`), `bundle has ${name}`)
    for (const p of bundle.filter((p) => !p.includes('/local-cards/'))) assert.doesNotMatch(p, FORBIDDEN_PATH, `bundle: excluded path ${p}`)

    // Deterministic: a second build is byte-identical.
    const again = build()
    try {
      for (const f of readdirSync(out)) assert.ok(readFileSync(join(out, f)).equals(readFileSync(join(again, f))), `${f} is reproducible`)
    } finally { rmSync(again, { recursive: true, force: true }) }
  } finally { if (existsSync(out)) rmSync(out, { recursive: true, force: true }) }
})
