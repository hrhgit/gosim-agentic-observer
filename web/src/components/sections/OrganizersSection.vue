<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isSupabaseConfigured } from '../../lib/supabase'
import { loadAnnouncements, type Announcement } from '../../lib/data'
import { fmtUtc } from '../../lib/format'
import OrganizersBody from './OrganizersBody.vue'

const { t, pick } = useI18n()

const announcements = ref<Announcement[]>([])

onMounted(async () => {
  if (!isSupabaseConfigured) return
  try { announcements.value = await loadAnnouncements(5) } catch { announcements.value = [] }
})
</script>

<template>
  <section id="organizers" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="reveal">
        <span class="poster-kicker">{{ t('home.credibility.kicker') }}</span>
        <h2 class="section-title distressed-type mt-8">{{ t('home.credibility.title') }}</h2>
      </div>
      <OrganizersBody />
      <div v-if="announcements.length" class="reveal mt-14">
        <div class="flex items-center justify-between gap-4 rule-b pb-3">
          <span class="label">{{ t('home.announcements.kicker') }}</span>
          <router-link to="/announcements" class="label accent">{{ t('home.announcements.all') }} →</router-link>
        </div>
        <div v-for="a in announcements" :key="a.id" class="row-sweep flex flex-wrap items-center justify-between gap-3 border-b border-white/10 py-4 pl-3">
          <span class="text-text-primary">{{ pick(a.title_en, a.title_zh) || a.title_en }}</span>
          <span class="label">{{ fmtUtc(a.created_at).slice(0, 10) }}</span>
        </div>
      </div>
    </div>
  </section>
</template>
