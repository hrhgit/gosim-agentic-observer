<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useI18n } from '../composables/useI18n'
import PageHead from '../components/layout/PageHead.vue'
import MarkdownArticle, { type TocItem } from '../components/content/MarkdownArticle.vue'
import docsEn from '../content/docs.en.md?raw'
import docsZh from '../content/docs.zh.md?raw'

const { t, pick, locale } = useI18n()
// The guide's image paths carry a placeholder because the site can be deployed under a sub-path.
const source = computed(() => pick(docsEn, docsZh).replaceAll('__BASE_URL__', import.meta.env.BASE_URL))
const toc = ref<TocItem[]>([])
const chips = computed(() => toc.value.filter(item => item.level === 2))
const activeId = ref('')
let observer: IntersectionObserver | undefined

// Scroll-spy on the h2 anchors: the topmost heading that has crossed the header line is active.
function observe(items: TocItem[]) {
  observer?.disconnect()
  if (typeof IntersectionObserver === 'undefined' || !items.length) return
  const positions = new Map<string, boolean>()
  observer = new IntersectionObserver(entries => {
    for (const entry of entries) positions.set((entry.target as HTMLElement).id, entry.isIntersecting || entry.boundingClientRect.top < 120)
    const passed = items.filter(item => item.level === 2 && positions.get(item.id))
    activeId.value = passed.length ? passed[passed.length - 1]!.id : items[0]!.id
  }, { rootMargin: '-120px 0px -70% 0px', threshold: [0, 1] })
  for (const item of items) { const el = document.getElementById(item.id); if (el) observer.observe(el) }
}
watch(toc, items => observe(items))
// Heading ids are localized, so a hash copied in one language does not exist after switching.
// Clear it and jump back to the top of the document instead of landing mid-page.
watch(locale, async () => {
  if (location.hash) history.replaceState(null, '', location.pathname + location.search)
  await nextTick()
  window.scrollTo({ top: 0 })
})
onUnmounted(() => observer?.disconnect())
</script>

<template>
  <main class="poster-canvas">
    <PageHead :kicker="t('docs_page.kicker')" :title="t('docs_page.title')" />
    <nav class="toc-chips" :aria-label="t('docs_page.toc')">
      <a v-for="item in chips" :key="item.id" :href="`#${item.id}`" :class="{ active: item.id === activeId }">{{ item.text }}</a>
    </nav>
    <section class="section"><div class="wrap">
      <div class="grid gap-12 lg:grid-cols-[15rem_1fr] lg:gap-16">
        <aside class="hidden lg:block">
          <nav class="toc" :aria-label="t('docs_page.toc')">
            <span class="label mb-2 block">{{ t('docs_page.toc') }}</span>
            <a v-for="item in toc" :key="item.id" :href="`#${item.id}`" :class="{ lvl3: item.level === 3, active: item.id === activeId }">{{ item.text }}</a>
          </nav>
        </aside>
        <div class="docs-body min-w-0">
          <MarkdownArticle :source="source" @toc="toc = $event" />
        </div>
      </div>
    </div></section>
  </main>
</template>
