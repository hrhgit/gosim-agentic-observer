// The v4 task cards participants can see (pure data, no Supabase client, so it can be unit-tested).
// Practice cards are public now: their pages ship with the site (web/src/content/taskcard.<id>.v4.<lang>.md).
// Hackathon cards A–D are fetched at run time from the 'scenarios' bucket, which serves their files only
// once the competition has started (migration 20260928004400, scripts/configure-v4-phases.py).
// The hidden cards E–H are never listed here: the site never names, links or requests them.

export type CardStage = 'practice' | 'formal'
export interface TaskCard { id: string; slug: string; stage: CardStage; symbol: string }

export const PRACTICE_CARDS: TaskCard[] = [
  { id: 'alpha', slug: 'v4-practice-alpha', stage: 'practice', symbol: 'α' },
  { id: 'beta', slug: 'v4-practice-beta', stage: 'practice', symbol: 'β' },
  { id: 'gamma', slug: 'v4-practice-gamma', stage: 'practice', symbol: 'γ' },
  { id: 'delta', slug: 'v4-practice-delta', stage: 'practice', symbol: 'δ' },
]
export const FORMAL_CARDS: TaskCard[] = ['a', 'b', 'c', 'd']
  .map(id => ({ id, slug: `v4-${id}`, stage: 'formal' as const, symbol: id.toUpperCase() }))

/** Bucket folders of a card that may be released; the bucket policy decides which ones a visitor can read. */
export const CARD_FOLDERS = ['config', 'public', 'truth'] as const
export type CardLanguage = 'zh' | 'en'
/** A released card's page in the bucket (formal cards): `<slug>/public/taskcard.<lang>.md`. */
export const cardPagePath = (language: CardLanguage) => `public/taskcard.${language}.md`

/** Practice cards whose page ships with the site, in card order (bundled keys: `../content/taskcard.<id>.v4.<lang>.md`). */
export function practiceCardsWithPages(bundledPaths: string[]): TaskCard[] {
  const ids = new Set(bundledPaths.map(path => /taskcard\.([a-z]+)\.v4\.(?:zh|en)\.md$/.exec(path)?.[1]).filter(Boolean))
  return PRACTICE_CARDS.filter(card => ids.has(card.id))
}

export function findCard(id: string | null | undefined): TaskCard | null {
  return [...PRACTICE_CARDS, ...FORMAL_CARDS].find(card => card.id === id) ?? null
}

/** The page title: the first `# ` heading of the card page, else the card symbol. */
export function cardTitle(markdown: string | null, card: TaskCard): string {
  return markdown?.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? card.symbol
}

/** ZIP entry name for a released file: unzipped into a cards/ folder it becomes cards/<id>/<folder>/<file>. */
export function cardZipEntry(card: TaskCard, key: string): string | null {
  return /^(config|public|truth)\/[A-Za-z0-9_.-]+$/.test(key) && !key.includes('..') ? `${card.id}/${key}` : null
}
