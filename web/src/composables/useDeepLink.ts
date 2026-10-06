import { watch } from 'vue'
import { useRoute } from 'vue-router'
import { deepLinkFor, tabFromQuery, type CompeteTab } from '../lib/deepLink'

// Follows deep links (lib/deepLink) on every navigation: waits for the page to load its data, opens the 参赛 tab,
// presses the button that reveals the target, expands any collapsed <details> around it, scrolls it under the
// sticky header and highlights it for a moment. Mounted once, in App.vue.
const WAIT_MS = 12000
const FLASH_MS = 2600

const q = (s: string) => document.querySelector<HTMLElement>(s)
const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function openTab(tab: CompeteTab | null) {
  const button = tab && q(`[data-testid="compete-tab-${tab}"]`)
  if (button && button.getAttribute('aria-selected') !== 'true') button.click()
}

export function useDeepLink() {
  const route = useRoute()
  let run = 0
  watch(() => [route.path, route.hash, route.query.tab] as const, async ([path, hash, tabQuery]) => {
    const mine = ++run
    const link = deepLinkFor(path, hash)
    const tab = path === '/compete' ? (link?.tab ?? tabFromQuery(tabQuery)) : null
    if (!link && !tab) return
    const end = Date.now() + WAIT_MS
    let el: HTMLElement | null = null
    while (Date.now() < end && mine === run) {
      if (tab) openTab(tab)
      const reveal = (link?.reveal ?? []).map(q).filter((b): b is HTMLElement => !!b)
      if (reveal.length) { reveal.forEach(b => b.click()); await sleep(150) }
      el = link ? link.targets.map(q).find(Boolean) ?? null : null
      // A tab-only link is done once the tabs exist.
      if (el || (!link && q('[data-testid="compete-tabs"]'))) break
      await sleep(250)
    }
    if (!el || mine !== run) return
    for (let d = el.closest('details'); d; d = d.parentElement?.closest('details') ?? null) d.open = true
    await sleep(60)
    const header = q('header')?.getBoundingClientRect().height ?? 0
    window.scrollTo({ top: Math.max(0, window.scrollY + el.getBoundingClientRect().top - header - 16), behavior: reduced() ? 'auto' : 'smooth' })
    el.classList.add('deep-link-flash')
    setTimeout(() => el!.classList.remove('deep-link-flash'), FLASH_MS)
  }, { immediate: true })
}
