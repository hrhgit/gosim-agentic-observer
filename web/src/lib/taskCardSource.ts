// Where a task card's page and files come from. Only the practice cards are bundled into the site
// (an explicit list, so a formal or hidden card page added to web/src/content can never ship);
// everything else is read from the 'scenarios' bucket, whose policy releases a card's files on schedule.
import { zipSync } from 'fflate'
import { supabase } from './supabase'
import { triggerDownload } from './storage'
import { cached } from './requestCache'
import { CARD_FOLDERS, PRACTICE_CARDS, cardPagePath, cardTitle, cardZipEntry, practiceCardsWithPages, type CardLanguage, type TaskCard } from './taskCards'

// Listing a card's bucket folders is expensive (storage.list scans the bucket), and the resources
// and task-card pages list every visible card on mount. Released files rarely change within a
// visit, so cache the listing in sessionStorage for a while instead of re-listing on every mount.
const RELEASED_FILES_CACHE_MS = 10 * 60 * 1000
const sessionCache = typeof window !== 'undefined' ? window.sessionStorage : undefined

const bundled = import.meta.glob('../content/taskcard.{alpha,beta,gamma,delta}.v4.{zh,en}.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

export const practiceCards = practiceCardsWithPages(Object.keys(bundled))

function bundledPage(card: TaskCard, language: CardLanguage): string | null {
  if (!PRACTICE_CARDS.includes(card)) return null
  return bundled[`../content/taskcard.${card.id}.v4.${language}.md`] ?? null
}

/** Title of a bundled card page ("练习卡 α"); formal cards only have their symbol until released. */
export function bundledCardTitle(card: TaskCard, language: CardLanguage): string {
  return cardTitle(bundledPage(card, language), card)
}

/** Released files of a card as `<folder>/<file>`; empty while the bucket withholds them. */
export async function releasedCardFiles(card: TaskCard): Promise<string[]> {
  return cached(`card-files:${card.slug}`, RELEASED_FILES_CACHE_MS, async () => {
    const lists = await Promise.all(CARD_FOLDERS.map(async folder => {
      const { data, error } = await supabase.storage.from('scenarios').list(`${card.slug}/${folder}`, { limit: 1000 })
      if (error || !data) return []
      return data.filter(item => item.id || item.metadata).map(item => `${folder}/${item.name}`)
    }))
    return lists.flat().filter(key => cardZipEntry(card, key)).sort()
  }, sessionCache)
}

/** The card page in the requested language (falling back to the other one), or null while it is not released. */
export async function cardPage(card: TaskCard, language: CardLanguage, files?: string[]): Promise<string | null> {
  const other: CardLanguage = language === 'zh' ? 'en' : 'zh'
  const local = bundledPage(card, language) ?? bundledPage(card, other)
  if (local) return local
  const released = files ?? await releasedCardFiles(card)
  for (const lang of [language, other]) {
    const key = cardPagePath(lang)
    if (!released.includes(key)) continue
    const { data } = await supabase.storage.from('scenarios').download(`${card.slug}/${key}`)
    if (data) return data.text()
  }
  return null
}

/** One ZIP of every released file of the card, laid out as cards/<id>/…. */
export async function downloadCardZip(card: TaskCard, files: string[]): Promise<void> {
  const entries: Record<string, Uint8Array> = {}
  for (const key of files) {
    const name = cardZipEntry(card, key)
    if (!name) continue
    const { data, error } = await supabase.storage.from('scenarios').download(`${card.slug}/${key}`)
    if (error || !data) throw error ?? new Error('download_failed')
    entries[name] = new Uint8Array(await data.arrayBuffer())
  }
  if (!Object.keys(entries).length) throw new Error('no_files')
  const zip = zipSync(entries, { level: 6 })
  triggerDownload(new Blob([zip], { type: 'application/zip' }), `taskcard-${card.id}.zip`)
}
