// Per-example downloads for the Resources page's "Download ZIP" buttons:
//   public/downloads/examples/<name>.zip  ->  <name>/ (../examples/<name>/ + a generated docs/)
// served at /downloads/examples/<name>.zip. Every example folder is picked up automatically
// (see examples-common.mjs). Deterministic: sorted entries, fixed mtime; .env*, build output and
// VCS folders excluded. web/tests/exampleZips.test.ts checks the contents.
import { writeFileSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { zipSync } from 'fflate'
import { addDocs, addTree, discoverExamples, downloadsDir, examplesRoot, mtime } from './examples-common.mjs'

mkdirSync(downloadsDir, { recursive: true })
for (const name of discoverExamples()) {
  const entries = {}
  addTree(entries, resolve(examplesRoot, name), name)
  const count = Object.keys(entries).length
  addDocs(entries, name)
  const zip = zipSync(entries, { level: 9, mtime })
  const outPath = resolve(downloadsDir, `${name}.zip`)
  writeFileSync(outPath, zip)
  console.log(`[build-examples] ${name}: ${count} project files + docs -> ${outPath} (${zip.length} bytes)`)
}
