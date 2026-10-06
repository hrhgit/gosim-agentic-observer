<script setup lang="ts">
// Guide of the survey26 command-line tool (also published as survey26-AGENTS.md for coding agents).
import { computed, ref } from 'vue'
import { useI18n } from '../composables/useI18n'
import PageHead from '../components/layout/PageHead.vue'
import MarkdownArticle, { type TocItem } from '../components/content/MarkdownArticle.vue'
import cliEn from '../content/cli.en.md?raw'
import cliZh from '../content/cli.zh.md?raw'

const { t, pick } = useI18n()
// The page heading replaces the document's own title line.
const source = computed(() => pick(cliEn, cliZh).replace(/^# .*\n+/, '').replaceAll('__BASE_URL__', import.meta.env.BASE_URL))
const toc = ref<TocItem[]>([])
const chips = computed(() => toc.value.filter(item => item.level === 2))
</script>

<template>
  <main class="poster-canvas">
    <PageHead :kicker="t('meta.pages.cli.kicker')" :title="t('meta.pages.cli.title')" />
    <nav class="toc-chips" :aria-label="t('docs_page.toc')">
      <a v-for="item in chips" :key="item.id" :href="`#${item.id}`">{{ item.text }}</a>
    </nav>
    <section class="section"><div class="wrap">
      <div class="docs-body min-w-0">
        <MarkdownArticle :source="source" @toc="toc = $event" />
      </div>
    </div></section>
  </main>
</template>
