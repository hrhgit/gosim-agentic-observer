// Regenerates public/fonts/NotoSansSC-{400,500,600,700}.woff2: Noto Sans SC subsetted down to just
// the CJK/kana characters that actually appear in our bundled zh + ja content, instead of shipping
// (or fetching from Google) the full ~10 MB-per-weight font. Re-run this whenever zh.json, ja.json,
// or the zh content/docs markdown gains characters it didn't have before — a character missing from
// the subset isn't a bug, it just falls back to the next font in the stack (see src/assets/styles/
// fonts.css), so this only needs to be re-run to keep that fallback rare, not to avoid breakage.
//
// Requires Python 3 with fontTools + brotli (one-time): python3 -m venv .fontenv &&
//   .fontenv/bin/pip install fonttools brotli && .fontenv/bin/python scripts/subset-noto-sans-sc.mjs
// (or point FONTTOOLS_PYTHON at an existing interpreter that already has them installed).
//
// Downloads the upstream variable font fresh each run rather than vendoring a 17 MB source file in
// the repo; only the ~1 MB of subsetted output is committed.
import { readFileSync, writeFileSync, mkdirSync, existsSync, mkdtempSync, rmSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { execFileSync } from 'node:child_process'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const SOURCE_URL = 'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf'
const WEIGHTS = [400, 500, 600, 700]
// Punctuation/digits commonly mixed into CJK copy, on top of whatever characters content extraction finds.
const EXTRA_TEXT = '0123456789.,!?:;()[]{}"\'`~@#$%^&*-_+=/\\|<>·、。「」『』'

function collectCjkChars() {
  const chars = new Set()
  const addText = (text) => {
    for (const ch of text) if (ch.codePointAt(0) > 0x2e7f) chars.add(ch)
  }
  const walkJson = (value) => {
    if (typeof value === 'string') addText(value)
    else if (Array.isArray(value)) value.forEach(walkJson)
    else if (value && typeof value === 'object') Object.values(value).forEach(walkJson)
  }
  walkJson(JSON.parse(readFileSync(resolve(root, 'src/i18n/zh.json'), 'utf8')))
  walkJson(JSON.parse(readFileSync(resolve(root, 'src/i18n/ja.json'), 'utf8')))
  const mdFiles = [
    'src/content/docs.zh.md', 'src/content/rules.zh.md',
    'src/content/taskcard.alpha.v4.zh.md', 'src/content/taskcard.beta.v4.zh.md',
    'src/content/taskcard.gamma.v4.zh.md', 'src/content/taskcard.delta.v4.zh.md',
    'src/content/taskcard.template.v4.zh.md',
  ]
  for (const f of mdFiles) {
    const p = resolve(root, f)
    if (existsSync(p)) addText(readFileSync(p, 'utf8'))
  }
  for (const ch of EXTRA_TEXT) chars.add(ch)
  return [...chars].sort().join('')
}

const pythonBin = process.env.FONTTOOLS_PYTHON || join(root, '.fontenv', 'bin', 'python')
if (!existsSync(pythonBin)) {
  console.error(`No Python interpreter at ${pythonBin}. Create it first:\n` +
    '  python3 -m venv .fontenv && .fontenv/bin/pip install fonttools brotli\n' +
    'or set FONTTOOLS_PYTHON to an interpreter that already has fontTools + brotli installed.')
  process.exit(1)
}

const chars = collectCjkChars()
console.log(`Collected ${chars.length} unique CJK/kana characters (+ punctuation) from zh/ja content.`)

const work = mkdtempSync(join(tmpdir(), 'notosanssc-'))
const sourceTtf = join(work, 'NotoSansSC-VF.ttf')
const charsFile = join(work, 'chars.txt')
writeFileSync(charsFile, chars, 'utf8')

console.log('Downloading upstream variable font (one-time, ~17 MB)...')
execFileSync('curl', ['-sL', '-C', '-', SOURCE_URL, '-o', sourceTtf, '--max-time', '120', '--retry', '5', '--retry-all-errors'], { stdio: 'inherit' })

const outDir = resolve(root, 'public/fonts')
mkdirSync(outDir, { recursive: true })

for (const weight of WEIGHTS) {
  const instanceTtf = join(work, `instance-${weight}.ttf`)
  execFileSync(pythonBin, ['-m', 'fontTools.varLib.instancer', sourceTtf, `wght=${weight}`, '-o', instanceTtf], { stdio: 'inherit' })
  execFileSync(pythonBin, [
    '-m', 'fontTools.subset', instanceTtf,
    `--unicodes-file=${charsToUnicodesFile(charsFile, work)}`,
    "--layout-features=*",
    '--flavor=woff2',
    `--output-file=${resolve(outDir, `NotoSansSC-${weight}.woff2`)}`,
    '--no-hinting',
  ], { stdio: 'inherit' })
  console.log(`  wrote public/fonts/NotoSansSC-${weight}.woff2`)
}

function charsToUnicodesFile(path, workDir) {
  const text = readFileSync(path, 'utf8')
  const out = join(workDir, 'unicodes.txt')
  const codes = [...text].map((ch) => `U+${ch.codePointAt(0).toString(16)}`).join(',')
  writeFileSync(out, codes, 'utf8')
  return out
}

rmSync(work, { recursive: true, force: true })
console.log('Done. Update the unicode-range comment in src/assets/styles/fonts.css if the character blocks covered changed.')
