// Is there any program in a project ZIP? Checked in the browser before uploading (and again by
// the server, supabase/functions/_shared/observer-source-check.ts, which this mirrors): a ZIP
// with neither observer.project.json nor a recognizable source file outside hidden folders can
// never be prepared, so it is refused at once and uses no preparation.
export const SOURCE_EXTENSIONS = new Set([
  'py', 'pyw', 'ipynb', 'ts', 'tsx', 'js', 'jsx', 'mjs', 'cjs', 'rs', 'go', 'java', 'kt', 'kts', 'scala', 'rb',
  'php', 'c', 'cc', 'cpp', 'cxx', 'h', 'hh', 'hpp', 'cs', 'fs', 'swift', 'm', 'mm', 'lua', 'r', 'jl', 'ex', 'exs',
  'erl', 'clj', 'dart', 'zig', 'nim', 'pl', 'pm', 'ml', 'hs', 'sh', 'bash', 'ps1', 'groovy', 'v', 'sol', 'wasm',
])
export const PROJECT_FILES = new Set([
  'dockerfile', 'makefile', 'cargo.toml', 'package.json', 'go.mod', 'pyproject.toml', 'requirements.txt',
  'setup.py', 'pom.xml', 'build.gradle', 'build.gradle.kts', 'gemfile', 'composer.json', 'deno.json',
])

export type SourceCheck = { manifest: boolean; code: boolean; files: string[] }

export function inspectSourceNames(names: string[]): SourceCheck {
  const files = names.map(n => n.replace(/\\/g, '/').replace(/^\.\//, ''))
    .filter(n => n && !n.endsWith('/') && !n.startsWith('__MACOSX/'))
  let manifest = false, code = false
  for (const name of files) {
    const parts = name.split('/')
    if (parts.some(p => p.startsWith('.'))) continue
    const base = parts[parts.length - 1].toLowerCase()
    if (base === 'observer.project.json') manifest = true
    const dot = base.lastIndexOf('.')
    if (PROJECT_FILES.has(base) || (dot > 0 && SOURCE_EXTENSIONS.has(base.slice(dot + 1)))) code = true
  }
  return { manifest, code, files: files.slice(0, 20) }
}

/** Entry names from a ZIP's central directory; null when it cannot be read (the server checks again). */
export function zipEntryNames(buffer: ArrayBuffer): string[] | null {
  const v = new DataView(buffer), bytes = new Uint8Array(buffer)
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 65535); i--) {
    if (v.getUint32(i, true) !== 0x06054b50) continue
    const count = v.getUint16(i + 10, true), offset = v.getUint32(i + 16, true)
    if (count === 0xffff || offset === 0xffffffff) return null
    const names: string[] = []
    let p = offset
    for (let n = 0; n < count; n++) {
      if (p + 46 > bytes.length || v.getUint32(p, true) !== 0x02014b50) return null
      const length = v.getUint16(p + 28, true), extra = v.getUint16(p + 30, true), note = v.getUint16(p + 32, true)
      names.push(new TextDecoder().decode(bytes.subarray(p + 46, p + 46 + length)))
      p += 46 + length + extra + note
    }
    return names
  }
  return null
}

/** Thrown before uploading (or rebuilt from the server's answer) with the files that were seen. */
export class ZipWithoutCodeError extends Error {
  files: string[]
  constructor(files: string[]) { super('zip_has_no_code'); this.files = files }
}
