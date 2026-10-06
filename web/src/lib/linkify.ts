export type TextSegment =
  | { kind: 'text'; text: string }
  | { kind: 'link'; href: string }
  | { kind: 'image'; href: string }

const URL_RE = /https?:\/\/[^\s<>"'，。；、）)]+/g
const IMAGE_RE = /\.(?:jpe?g|png|webp|gif)$/i

/** Split plain text into text, link and image segments so templates can render URLs without v-html. */
export function linkSegments(value: string | null | undefined): TextSegment[] {
  const text = value ?? ''
  const out: TextSegment[] = []
  let last = 0
  for (const match of text.matchAll(URL_RE)) {
    const href = match[0].replace(/[.,:;!?]+$/, '')
    const start = match.index!
    if (start > last) out.push({ kind: 'text', text: text.slice(last, start) })
    out.push({ kind: IMAGE_RE.test(href) ? 'image' : 'link', href })
    last = start + href.length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
  return out
}
