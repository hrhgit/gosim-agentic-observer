<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import markedKatex from 'marked-katex-extension'
import { useRouter } from 'vue-router'
import 'katex/dist/katex.min.css'

marked.use(markedKatex({ throwOnError: false, nonStandard: true }))

export interface TocItem { id: string; text: string; level: number }
const props = defineProps<{ source: string }>()
const emit = defineEmits<{ toc: [items: TocItem[]] }>()
const root = ref<HTMLElement | null>(null)
const router = useRouter()
const html = computed(() => marked.parse(props.source, { async: false, gfm: true, breaks: false }) as string)

function slugify(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '') || 'section'
}

function decorate() {
  const items: TocItem[] = []
  const seen = new Set<string>()
  root.value?.querySelectorAll<HTMLElement>('h2, h3').forEach(heading => {
    const text = heading.textContent?.trim() ?? ''
    let id = slugify(text)
    while (seen.has(id)) id = `${id}-x`
    seen.add(id)
    heading.id = id
    items.push({ id, text, level: heading.tagName === 'H2' ? 2 : 3 })
  })
  // Site-relative links in the Markdown ("/resources") belong to the app, which lives under a base path.
  root.value?.querySelectorAll<HTMLAnchorElement>('a[href^="/"]:not([href^="//"])').forEach((a) => {
    const path = a.getAttribute('href') ?? '/'
    a.href = router.resolve(path).href
    a.addEventListener('click', (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      event.preventDefault()
      void router.push(path)
    })
  })
  root.value?.querySelectorAll<HTMLElement>('pre').forEach((pre) => { if (!pre.hasAttribute('tabindex')) pre.setAttribute('tabindex', '0') })
  emit('toc', items)
}

watch(html, () => { void nextTick(decorate) })
onMounted(decorate)
</script>

<template>
  <article ref="root" class="prose" v-html="html"></article>
</template>
