// A small, safe markdown subset for announcements: **bold**, `code`, [label](https://…), bare URLs
// (image URLs become images), "- " / "1. " lists and "#" headings (shown bold). The result is plain data
// that templates render with text interpolation only — never v-html — so it is safe by construction.
// Only http(s) links are produced; anything else stays literal text.
import { linkSegments } from './linkify.ts'

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'link'; href: string; text: string }
  | { kind: 'image'; href: string }

export type Block =
  | { kind: 'para'; inlines: Inline[] }
  | { kind: 'heading'; inlines: Inline[] }
  | { kind: 'ul' | 'ol'; items: Inline[][] }

const INLINE_RE = /`([^`\n]+)`|\*\*([^*\n](?:[^\n]*?[^*\n])?)\*\*|\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g

function plain(text: string): Inline[] {
  return linkSegments(text).map(seg => seg.kind === 'text' ? seg
    : seg.kind === 'link' ? { kind: 'link', href: seg.href, text: seg.href } : { kind: 'image', href: seg.href })
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let last = 0
  for (const m of text.matchAll(INLINE_RE)) {
    if (m.index! > last) out.push(...plain(text.slice(last, m.index)))
    if (m[1] !== undefined) out.push({ kind: 'code', text: m[1] })
    else if (m[2] !== undefined) out.push({ kind: 'bold', text: m[2] })
    else out.push({ kind: 'link', text: m[3], href: m[4] })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push(...plain(text.slice(last)))
  // Merge neighbouring text so a paragraph keeps its line breaks in one run.
  return out.reduce<Inline[]>((acc, x) => {
    const prev = acc[acc.length - 1]
    if (x.kind === 'text' && prev?.kind === 'text') prev.text += x.text
    else acc.push(x.kind === 'text' ? { ...x } : x)
    return acc
  }, [])
}

const UL = /^\s*[-*•]\s+(.*)$/
const OL = /^\s*\d+[.)、]\s+(.*)$/
const HEADING = /^\s*#{1,6}\s+(.*)$/

export function parseAnnouncement(value: string | null | undefined): Block[] {
  const lines = (value ?? '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let para: string[] = []
  const flush = () => {
    const text = para.join('\n').replace(/^\n+|\n+$/g, '')
    if (text) blocks.push({ kind: 'para', inlines: parseInline(text) })
    para = []
  }
  for (const line of lines) {
    const ul = UL.exec(line), ol = ul ? null : OL.exec(line), h = HEADING.exec(line)
    const list = ul ? 'ul' : ol ? 'ol' : null
    if (list) {
      flush()
      const prev = blocks[blocks.length - 1]
      const item = parseInline((ul ?? ol)![1])
      if (prev && prev.kind === list) prev.items.push(item)
      else blocks.push({ kind: list, items: [item] })
    } else if (h) {
      flush()
      blocks.push({ kind: 'heading', inlines: parseInline(h[1]) })
    } else if (!line.trim()) {
      flush()
    } else para.push(line)
  }
  flush()
  return blocks
}
