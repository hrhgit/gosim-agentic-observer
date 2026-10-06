import { ref, reactive, provide, inject, watch, type InjectionKey, type Ref } from 'vue'
import en from '../i18n/en'
import zh from '../i18n/zh'

type Messages = Record<string, any>
export type Locale = 'en' | 'zh' | 'ja' | 'fr'

export interface I18n {
  locale: Ref<Locale>
  t: (key: string) => any
  tf: (key: string, params: Record<string, string | number>) => string
  pick: <T>(english: T, chinese: T) => T
  toggleLocale: () => void
  setLocale: (value: Locale) => void
}

const I18N_KEY: InjectionKey<I18n> = Symbol('i18n')
// Partial locales are filled with English first so they never acquire sparse
// lists or lose English fields. Each message lives exactly once per locale.
function withEnglishDefaults(translated: Messages): Messages {
  function merge(base: Messages, overlay: Messages): Messages {
    for (const [key,value] of Object.entries(overlay)) {
      base[key]=value && typeof value==='object' && !Array.isArray(value)
        ? merge(base[key] && typeof base[key]==='object' && !Array.isArray(base[key]) ? base[key] : {},value)
        : structuredClone(value)
    }
    return base
  }
  return merge(structuredClone(en),translated)
}
// ja/fr are a minority of visits (most users are zh, with en as the universal fallback), so their
// ~100 KB of JSON loads on demand instead of riding in the eager bundle every visitor pays for.
// Until loaded they point straight at `en`, which is exactly what withEnglishDefaults(ja/fr) reduces
// to before any locale-specific override is merged in — so the fallback reads identically either way.
const messages = reactive<Record<Locale, Messages>>({ en, zh, ja: en, fr: en })
const extraLoaded = new Set<Locale>()
function ensureLocaleLoaded(locale: Locale) {
  if ((locale !== 'ja' && locale !== 'fr') || extraLoaded.has(locale)) return
  extraLoaded.add(locale)
  const loader = locale === 'ja' ? import('../i18n/ja.json') : import('../i18n/fr.json')
  void loader.then(({ default: raw }) => { messages[locale] = withEnglishDefaults(raw) })
}
const STORAGE_KEY = 'agent-observer-locale'

export const LOCALES: Locale[] = ['zh', 'en', 'ja', 'fr']
export const LOCALE_NAMES: Record<Locale, string> = { zh: '中文', en: 'EN', ja: '日本語', fr: 'FR' }

/** Lookup order per locale: ja/fr carry the first-visit surface and fall back to English, then Chinese. */
const CHAIN: Record<Locale, Locale[]> = {
  zh: ['zh', 'en'],
  en: ['en', 'zh'],
  ja: ['ja', 'en', 'zh'],
  fr: ['fr', 'en', 'zh'],
}
const HTML_LANG: Record<Locale, string> = { zh: 'zh-CN', en: 'en', ja: 'ja', fr: 'fr' }

function lookup(obj: any, path: string): any {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj)
}

export function translate(locale: Locale, key: string): any {
  for (const step of CHAIN[locale]) {
    const value = lookup(messages[step], key)
    if (value !== undefined) return value
  }
  return key
}

export function interpolate(template: unknown, params: Record<string, string | number>): string {
  const text = typeof template === 'string' ? template : String(template)
  return text.replace(/\{(\w+)\}/g, (match, name) => (name in params ? String(params[name]) : match))
}

function asLocale(value: unknown): Locale | null {
  return LOCALES.includes(value as Locale) ? (value as Locale) : null
}

/** Negotiation order: ?lang= → saved preference → browser language → zh. */
export function negotiateLocale(): Locale {
  if (typeof window === 'undefined') return 'zh'
  const query = asLocale(new URLSearchParams(window.location.search).get('lang'))
  if (query) return query
  const saved = asLocale(window.localStorage.getItem(STORAGE_KEY) || window.localStorage.getItem('cosmos-locale'))
  if (saved) return saved
  for (const tag of navigator.languages ?? [navigator.language]) {
    const lower = String(tag || '').toLowerCase()
    if (lower.startsWith('zh')) return 'zh'
    if (lower.startsWith('ja')) return 'ja'
    if (lower.startsWith('fr')) return 'fr'
    if (lower.startsWith('en')) return 'en'
  }
  return 'zh'
}

/** Locale readable outside components (router hooks); provideI18n keeps it in sync. */
export const currentLocale = ref<Locale>('zh')
let currentPage = 'home'

/** Set document.title + description for a route page key (see meta.pages in the i18n files). */
export function applyDocumentMeta(page: string): void {
  if (typeof document === 'undefined') return
  currentPage = page
  const locale = currentLocale.value
  const entry = translate(locale, `meta.pages.${page}`)
  const known = entry && typeof entry === 'object'
  const brand = translate(locale, 'meta.brand')
  document.title = page === 'home' || !known ? translate(locale, 'meta.title') : `${entry.title} · ${brand}`
  const description = document.querySelector<HTMLMetaElement>('meta[name="description"]')
  if (description) description.content = known && entry.description ? entry.description : translate(locale, 'meta.description')
}

export function provideI18n(): I18n {
  const locale = ref<Locale>(negotiateLocale())

  if (typeof document !== 'undefined') {
    watch(locale, (value) => {
      currentLocale.value = value
      document.documentElement.lang = HTML_LANG[value]
      applyDocumentMeta(currentPage)
      window.localStorage.setItem(STORAGE_KEY, value)
      ensureLocaleLoaded(value)
    }, { immediate: true })
  }

  const t = (key: string) => translate(locale.value, key)
  const tf = (key: string, params: Record<string, string | number>) => interpolate(translate(locale.value, key), params)
  const pick = <T>(english: T, chinese: T): T => (locale.value === 'zh' ? chinese : english)
  const setLocale = (value: Locale) => { locale.value = value }
  const toggleLocale = () => { locale.value = LOCALES[(LOCALES.indexOf(locale.value) + 1) % LOCALES.length]! }

  const api: I18n = { locale, t, tf, pick, toggleLocale, setLocale }
  provide(I18N_KEY, api)
  return api
}

export function useI18n(): I18n {
  const i18n = inject(I18N_KEY)
  if (!i18n) throw new Error('useI18n() called without provideI18n()')
  return i18n
}
