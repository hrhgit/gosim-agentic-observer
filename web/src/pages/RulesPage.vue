<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'
import PageHead from '../components/layout/PageHead.vue'
import MarkdownArticle from '../components/content/MarkdownArticle.vue'
import rulesEn from '../content/rules.en.md?raw'
import rulesZh from '../content/rules.zh.md?raw'

const { t, pick } = useI18n()
const source = computed(() => pick(rulesEn, rulesZh))
</script>

<template>
  <main class="poster-canvas page-read">
    <PageHead :kicker="t('rules_page.kicker')" :title="t('rules_page.title')" :note="t('rules_page.version')" />
    <section class="section"><div class="wrap">
      <MarkdownArticle :source="source" />
    </div></section>
  </main>
</template>
