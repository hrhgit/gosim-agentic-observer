<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from '../composables/useI18n'
import { isSupabaseConfigured } from '../lib/supabase'
import { loadAnnouncements, type Announcement } from '../lib/data'
import { fmtUtc } from '../lib/format'
import AnnouncementBody from '../components/content/AnnouncementBody.vue'
import PageHead from '../components/layout/PageHead.vue'

const { t, pick } = useI18n()
const rows = ref<Announcement[]>([])
const loading = ref(true)
onMounted(async () => {
  try { rows.value = isSupabaseConfigured ? await loadAnnouncements() : [] }
  catch { rows.value = [] }
  finally { loading.value = false }
})
</script>

<template>
  <main class="poster-canvas">
    <PageHead :kicker="t('ann.kicker')" :title="t('ann.title')" />
    <section class="section"><div class="wrap-narrow">
      <p v-if="loading" class="text3 text-sm">{{ t('common.loading') }}</p>
      <div v-else-if="!rows.length">
        <p class="text2">{{ t('ann.empty') }}</p>
        <p class="text3 mt-4 text-sm">{{ t('ann.empty_hint') }} <router-link class="accent-l underline underline-offset-2" to="/resources">{{ t('nav.resources') }} →</router-link></p>
      </div>
      <article v-for="a in rows" :key="a.id" class="rule-t py-8">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <span class="label">{{ fmtUtc(a.created_at) }} UTC <template v-if="a.is_pinned">· <span class="accent-l">{{ t('ann.pinned') }}</span></template></span>
          <span class="pill" :class="a.level">{{ t(`ann.levels.${a.level}`) }}</span>
        </div>
        <h2 class="mt-3 text-2xl font-semibold tracking-[-.03em] text-text-primary">{{ pick(a.title_en, a.title_zh) || a.title_en }}</h2>
        <AnnouncementBody class="text2 mt-3" :text="pick(a.body_en, a.body_zh) || a.body_en" />
      </article>
    </div></section>
  </main>
</template>
