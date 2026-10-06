// Shared by build-examples.mjs (per-example zips) and build-examples-bundle.mjs (all-in-one zip).
// Every folder under ../examples/ that is not _-/.-prefixed and has AGENTS.md, README.md and
// observer.project.json is an example; new ones (e.g. typescript-pro) are picked up with no code change.
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

export const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
export const examplesRoot = resolve(webRoot, '..', 'examples')
export const downloadsDir = process.env.EXAMPLES_OUT_DIR
  ? resolve(process.env.EXAMPLES_OUT_DIR)
  : resolve(webRoot, 'public', 'downloads', 'examples')
export const mtime = new Date('2026-10-01T00:00:00Z')
export const REQUIRED = ['AGENTS.md', 'README.md', 'observer.project.json']

const EXCLUDED_DIRS = new Set(['node_modules', 'dist', 'target', '__pycache__', '.git', '.pytest_cache', '.idea', '.vscode', 'run_output', '.venv', '.secrets', '.npm-cache'])
const EXCLUDED_FILES = new Set(['.DS_Store', 'Thumbs.db'])
const excludeFile = (name) => EXCLUDED_FILES.has(name) || name.endsWith('.pyc') || name.endsWith('.pyo') || name.endsWith('.zip')
  || name === '.env' || (name.startsWith('.env.') && !['.env.example', '.env.sample', '.env.template'].includes(name))
const byName = (a, b) => (a < b ? -1 : a > b ? 1 : 0)

export function discoverExamples() {
  const names = readdirSync(examplesRoot, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith('_') && !e.name.startsWith('.'))
    .map((e) => e.name).sort(byName)
  for (const name of names) {
    const missing = REQUIRED.filter((f) => !existsSync(resolve(examplesRoot, name, f)))
    if (missing.length) throw new Error(`examples/${name} is incomplete: missing ${missing.join(', ')}`)
  }
  return names
}

export function walk(dir, rel = '') {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => byName(a.name, b.name))) {
    const relPath = rel ? `${rel}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      if (!EXCLUDED_DIRS.has(entry.name)) out.push(...walk(resolve(dir, entry.name), relPath))
    } else if (entry.isFile() && !excludeFile(entry.name)) {
      out.push(relPath)
    }
  }
  return out
}

export function addTree(entries, sourceDir, zipPrefix) {
  for (const rel of walk(sourceDir)) entries[`${zipPrefix}/${rel}`] = [readFileSync(resolve(sourceDir, rel)), { mtime, level: 9 }]
}

// Participant guide (zh + en, with images) generated from the site's single copy under <prefix>/docs/.
export function addDocs(entries, prefix) {
  const contentDir = resolve(webRoot, 'src', 'content')
  const mediaDir = resolve(webRoot, 'public', 'media', 'docs', 'v4-guide')
  const rewriteImages = (raw) => raw.replaceAll('__BASE_URL__media/docs/v4-guide/', 'images/')
  for (const [lang, file] of [['zh', 'docs.zh.md'], ['en', 'docs.en.md']]) {
    const raw = readFileSync(resolve(contentDir, file), 'utf8')
    entries[`${prefix}/docs/participant-guide.${lang}.md`] = [Buffer.from(rewriteImages(raw), 'utf8'), { mtime, level: 9 }]
  }
  if (existsSync(mediaDir)) {
    for (const name of readdirSync(mediaDir).sort(byName)) {
      entries[`${prefix}/docs/images/${name}`] = [readFileSync(resolve(mediaDir, name)), { mtime, level: 9 }]
    }
  }
}
