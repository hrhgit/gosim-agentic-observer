<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { isSupabaseConfigured } from '../../lib/supabase'
import { loadParticipantsWall, loadParticipantsStats, type WallEntry, type ParticipantsStats } from '../../lib/data'
import { useAuth } from '../../stores/auth'
import TierBadge from '../TierBadge.vue'
import UserAvatar from '../UserAvatar.vue'

const { t, tf } = useI18n()
const { isLoggedIn } = useAuth()
const entries = ref<WallEntry[]>([])
const stats = ref<ParticipantsStats | null>(null)
const loaded = ref(false)

onMounted(async () => {
  if (!isSupabaseConfigured) { loaded.value = true; return }
  try {
    const [wall, s] = await Promise.all([loadParticipantsWall(24), loadParticipantsStats()])
    entries.value = wall
    stats.value = s
  } catch { /* the wall is decorative on the homepage; failures stay quiet */ }
  finally { loaded.value = true }
})

const marquee = computed(() => entries.value.length >= 8)
function lookingChip(e: WallEntry): string {
  if (e.seeking === 'astro') return tf('home.participants.looking_astro', { n: e.seeking_count || 1 })
  if (e.seeking === 'ai') return tf('home.participants.looking_ai', { n: e.seeking_count || 1 })
  return e.looking_for_team ? t('home.participants.looking') : ''
}
const roleLabel = (role: string | null) => {
  if (!role) return ''
  const known = t('auth.role_options') as Record<string, string>
  return known[role] ?? role
}
const statItems = computed(() => stats.value ? [
  { v: stats.value.total, l: t('home.participants.stat_total') },
  { v: stats.value.teams, l: t('home.participants.stat_teams') },
  { v: stats.value.looking, l: t('home.participants.stat_looking') },
] : [])
</script>

<template>
  <section id="participants" class="poster-section poster-canvas py-20 md:py-28" data-testid="participants-wall">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="reveal flex flex-wrap items-end justify-between gap-6">
        <div>
          <span class="poster-kicker kicker-amber kicker-xl">{{ t('home.participants.kicker') }}</span>
          <p class="lede mt-6 max-w-2xl">{{ t('home.participants.lede') }}</p>
        </div>
        <div v-if="statItems.length" class="wall-stats">
          <div v-for="s in statItems" :key="s.l"><b v-countup>{{ s.v }}</b><span>{{ s.l }}</span></div>
        </div>
      </div>

      <div v-if="entries.length >= 3" class="mt-12" :class="marquee ? 'wall-marquee-clip reveal' : 'reveal-stagger'">
        <div :class="marquee ? 'wall-marquee' : 'grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4'">
          <template v-if="marquee">
            <div class="wall-track" aria-hidden="false">
              <article v-for="e in entries" :key="e.id" class="wall-card">
                <div class="wall-card-head">
                  <UserAvatar :name="e.name" :github="e.github" :avatar-url="e.avatar_url" /><h3>{{ e.name }}</h3>
                  <span v-if="lookingChip(e)" class="wall-looking"><span class="live-dot h-1.5 w-1.5"></span>{{ lookingChip(e) }}</span>
                </div>
                <div class="wall-badges"><TierBadge kind="astro" :level="e.astro_level" /><TierBadge kind="ai" :level="e.ai_level" /></div>
                <p v-if="e.blurb" class="wall-blurb">“{{ e.blurb }}”</p>
                <p class="wall-meta">{{ [roleLabel(e.role), e.city, e.team_name ? tf('home.participants.in_team', { team: e.team_name }) : ''].filter(Boolean).join(' · ') }}</p>
              </article>
            </div>
            <div class="wall-track" aria-hidden="true">
              <article v-for="e in entries" :key="`dup-${e.id}`" class="wall-card">
                <div class="wall-card-head">
                  <UserAvatar :name="e.name" :github="e.github" :avatar-url="e.avatar_url" /><h3>{{ e.name }}</h3>
                  <span v-if="lookingChip(e)" class="wall-looking"><span class="live-dot h-1.5 w-1.5"></span>{{ lookingChip(e) }}</span>
                </div>
                <div class="wall-badges"><TierBadge kind="astro" :level="e.astro_level" /><TierBadge kind="ai" :level="e.ai_level" /></div>
                <p v-if="e.blurb" class="wall-blurb">“{{ e.blurb }}”</p>
                <p class="wall-meta">{{ [roleLabel(e.role), e.city, e.team_name ? tf('home.participants.in_team', { team: e.team_name }) : ''].filter(Boolean).join(' · ') }}</p>
              </article>
            </div>
          </template>
          <template v-else>
            <article v-for="e in entries" :key="e.id" class="wall-card">
              <div class="wall-card-head">
                <UserAvatar :name="e.name" :github="e.github" :avatar-url="e.avatar_url" /><h3>{{ e.name }}</h3>
                <span v-if="lookingChip(e)" class="wall-looking"><span class="live-dot h-1.5 w-1.5"></span>{{ lookingChip(e) }}</span>
              </div>
              <div class="wall-badges"><TierBadge kind="astro" :level="e.astro_level" /><TierBadge kind="ai" :level="e.ai_level" /></div>
              <p v-if="e.blurb" class="wall-blurb">“{{ e.blurb }}”</p>
              <p class="wall-meta">{{ [roleLabel(e.role), e.city, e.team_name ? tf('home.participants.in_team', { team: e.team_name }) : ''].filter(Boolean).join(' · ') }}</p>
            </article>
          </template>
        </div>
      </div>

      <div v-else-if="loaded" class="wall-empty reveal mt-12">
        <p class="wall-empty-title">{{ t('home.participants.empty_title') }}</p>
        <p class="mt-2 text-sm text-[#9aa3b8]">{{ t('home.participants.empty_desc') }}</p>
      </div>

      <div class="reveal mt-10 flex flex-wrap gap-3">
        <router-link to="/teammates" class="btn primary" data-testid="wall-browse">{{ t('home.participants.cta_board') }} →</router-link>
        <router-link :to="isLoggedIn ? '/profile' : '/register'" class="btn">{{ isLoggedIn ? t('home.participants.cta_wall_profile') : t('home.participants.cta_wall_register') }} →</router-link>
      </div>
    </div>
  </section>
</template>
