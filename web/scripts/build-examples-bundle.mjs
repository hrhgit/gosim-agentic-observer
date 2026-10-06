// All-in-one examples bundle, built with the site (prebuild) and served at
//   /downloads/examples/gosim-observer-examples.zip
//     <name>/ for every example under ../examples/ (auto-discovered, see examples-common.mjs)
//     docs/          participant guide zh+en (generated from the site's copy)
//     local-cards/   L1-L4 fully public local practice cards (../examples/_local/cards)
//     runner/        public local scoring kit (../examples/_local/runner)
//     README.md, LICENSE.md
import { readFileSync, writeFileSync, mkdirSync, existsSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { zipSync } from 'fflate'
import { addDocs, addTree, discoverExamples, downloadsDir, examplesRoot, mtime } from './examples-common.mjs'

const ZIP_ROOT = 'gosim-observer-examples'
const localRoot = resolve(examplesRoot, '_local')
for (const required of ['cards', 'runner', 'BUNDLE_README.md']) {
  if (!existsSync(resolve(localRoot, required))) throw new Error(`[build-examples-bundle] missing examples/_local/${required}`)
}

const entries = {}
const names = discoverExamples()
for (const name of names) addTree(entries, resolve(examplesRoot, name), `${ZIP_ROOT}/${name}`)
for (const card of readdirSync(resolve(localRoot, 'cards')).sort()) {
  addTree(entries, resolve(localRoot, 'cards', card), `${ZIP_ROOT}/local-cards/${card}`)
}
addTree(entries, resolve(localRoot, 'runner'), `${ZIP_ROOT}/runner`)
entries[`${ZIP_ROOT}/README.md`] = [readFileSync(resolve(localRoot, 'BUNDLE_README.md')), { mtime, level: 9 }]
entries[`${ZIP_ROOT}/LICENSE.md`] = [readFileSync(resolve(examplesRoot, 'LICENSE.md')), { mtime, level: 9 }]
addDocs(entries, ZIP_ROOT)

const zip = zipSync(entries, { level: 9, mtime })
mkdirSync(downloadsDir, { recursive: true })
const outPath = resolve(downloadsDir, `${ZIP_ROOT}.zip`)
writeFileSync(outPath, zip)
console.log(`[build-examples-bundle] ${names.join(', ')} + local cards: ${Object.keys(entries).length} files -> ${outPath} (${zip.length} bytes)`)
