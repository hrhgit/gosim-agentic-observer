// Minimal in-browser spreadsheet reader for admin code imports (xlsx + csv).
// Files never leave the browser.
import { unzipSync, strFromU8 } from 'fflate'

export interface Sheet { name: string; rows: string[][] }

const NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'

function xml(text: string) { return new DOMParser().parseFromString(text, 'application/xml') }
function els(node: Document | Element, tag: string) { return Array.from(node.getElementsByTagNameNS(NS_MAIN, tag)) }
function colIndex(ref: string) {
  let n = 0
  for (const ch of ref.replace(/\d+$/, '').toUpperCase()) n = n * 26 + ch.charCodeAt(0) - 64
  return n - 1
}

export function parseCsv(text: string): string[][] {
  text = text.replace(/^﻿/, '')
  const firstLine = text.split(/\r?\n/, 1)[0] ?? ''
  const sep = (firstLine.match(/\t/g)?.length ?? 0) > (firstLine.match(/,/g)?.length ?? 0) ? '\t' : ','
  const rows: string[][] = []
  let row: string[] = [], cell = '', quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++ }
      else if (ch === '"') quoted = false
      else cell += ch
    } else if (ch === '"' && cell === '') quoted = true
    else if (ch === sep) { row.push(cell); cell = '' }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell); rows.push(row); row = []; cell = ''
    } else cell += ch
  }
  if (cell || row.length) { row.push(cell); rows.push(row) }
  return rows
}

export function parseXlsx(data: Uint8Array): Sheet[] {
  const files = unzipSync(data)
  const read = (p: string) => (files[p] ? strFromU8(files[p]) : '')
  const shared = read('xl/sharedStrings.xml')
    ? els(xml(read('xl/sharedStrings.xml')), 'si').map(si => els(si, 't').map(t => t.textContent ?? '').join(''))
    : []
  const rels = new Map<string, string>()
  for (const r of Array.from(xml(read('xl/_rels/workbook.xml.rels')).getElementsByTagName('Relationship'))) {
    const target = r.getAttribute('Target') ?? ''
    rels.set(r.getAttribute('Id') ?? '', target.startsWith('/') ? target.slice(1) : `xl/${target}`)
  }
  return els(xml(read('xl/workbook.xml')), 'sheet').map(s => {
    const rid = s.getAttribute('r:id') ?? s.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id') ?? ''
    const doc = xml(read(rels.get(rid) ?? ''))
    const rows: string[][] = []
    for (const r of els(doc, 'row')) {
      const ri = Number(r.getAttribute('r') ?? rows.length + 1) - 1
      const out: string[] = []
      els(r, 'c').forEach((c, i) => {
        const ref = c.getAttribute('r')
        const ci = ref ? colIndex(ref) : i
        const type = c.getAttribute('t')
        const v = els(c, 'v')[0]?.textContent ?? ''
        let val = v
        if (type === 's') val = shared[Number(v)] ?? ''
        else if (type === 'inlineStr') val = els(c, 't').map(t => t.textContent ?? '').join('')
        while (out.length < ci) out.push('')
        out[ci] = val
      })
      while (rows.length < ri) rows.push([])
      rows[ri] = out
    }
    return { name: s.getAttribute('name') ?? `Sheet${rows.length}`, rows }
  })
}

export async function readSheets(file: File): Promise<Sheet[]> {
  const lower = file.name.toLowerCase()
  if (lower.endsWith('.csv') || lower.endsWith('.tsv') || lower.endsWith('.txt')) return [{ name: file.name, rows: parseCsv(await file.text()) }]
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) throw new Error('unsupported')
  return parseXlsx(bytes)
}

export function maskCode(v: string) {
  const s = v.trim()
  if (!s) return ''
  return s.length <= 4 ? '****' : `${s.slice(0, 4)}****`
}

export function guessCodeColumn(header: string[]) {
  return header.findIndex(h => /码|code|兑换|激活|key/i.test(h))
}
