<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isSupabaseConfigured } from '../../lib/supabase'
import { loadAnnouncements, type Announcement } from '../../lib/data'

const { t, pick } = useI18n()
const banner = ref<Announcement | null>(null)

onMounted(async () => {
  if (!isSupabaseConfigured) return
  try {
    const rows = await loadAnnouncements(5)
    banner.value = rows.find(row => row.is_pinned) ?? null
  } catch { banner.value = null }
})
</script>

<template>
  <div v-if="banner" class="banner" :class="banner.level" data-testid="announcement-banner">
    <div class="wrap banner-in">
      <span class="tag">{{ t('ann.kicker') }}</span>
      <span class="min-w-0 flex-1">{{ pick(banner.title_en, banner.title_zh) || banner.title_en }}</span>
      <router-link to="/announcements" class="label accent whitespace-nowrap">{{ t('home.announcements.all') }} →</router-link>
    </div>
  </div>
</template>
