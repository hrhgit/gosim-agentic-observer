<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useLeaderboard } from '../../composables/useLeaderboard'

const { t, pick } = useI18n()
const { entries, loading, refreshing, error, updatedAt, reload, leaderboardUrl } = useLeaderboard(20)
const updated = computed(() => updatedAt.value?.toLocaleTimeString(pick('en-US', 'zh-CN')) || '')
const metric = (value: number | null) => value == null ? '—' : value.toFixed(2)
</script>

<template>
  <section id="leaderboard" class="poster-section poster-canvas py-24 md:py-40">
    <div class="leaderboard-aura plasma-field" aria-hidden="true"></div>
    <div class="relative z-10 mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.72fr_1.28fr] lg:gap-20">
        <div class="reveal">
          <span class="poster-kicker mt-14">{{ t('home.leaderboard.kicker') }}</span>
          <h2 class="section-title distressed-type mt-9">{{ t('home.leaderboard.title') }}</h2>
          <p class="mt-8 max-w-lg text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.leaderboard.lede') }}</p>

          <div class="mt-12 flex items-center gap-4 font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">
            <span class="signal-dot"></span>
            {{ loading ? pick('Connecting to platform', '正在连接比赛平台') : error ? pick('Awaiting benchmark signal', '等待基准信号') : pick('Competition platform connected', '比赛平台已连接') }}
          </div>
        </div>

        <div class="reveal reveal-delay-1 border-y poster-rule">
          <div class="flex items-center justify-between gap-4 border-b poster-rule py-5">
            <span class="font-mono text-xs uppercase tracking-[.1em] text-text-muted">
              {{ updated ? `${t('leaderboard.updated')} ${updated}` : pick('Live evaluation feed', '实时评测数据') }}
            </span>
            <div class="flex gap-5">
              <button class="font-mono text-xs uppercase tracking-[.1em] text-text-tertiary hover:text-[#edb28b] disabled:opacity-50" :disabled="refreshing" @click="reload">↻ {{ t('leaderboard.refresh') }}</button>
              <a v-if="leaderboardUrl !== '#'" :href="leaderboardUrl" target="_blank" rel="noopener" class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('leaderboard.full') }} ↗</a>
            </div>
          </div>

          <div v-if="loading || (error && !entries.length) || !entries.length" class="grid min-h-80 place-items-center py-16 text-center">
            <div>
              <div class="text-4xl font-semibold text-white/8">00</div>
              <p class="mt-4 max-w-sm text-sm leading-relaxed text-text-secondary">
                {{ loading ? t('leaderboard.loading') : error ? t('leaderboard.unavailable') : t('leaderboard.empty') }}
              </p>
            </div>
          </div>

          <div v-else class="overflow-x-auto">
            <table class="w-full min-w-[720px] border-collapse text-left">
              <thead><tr class="border-b poster-rule"><th class="table-label py-4">#</th><th class="table-label py-4">{{ t('leaderboard.team') }}</th><th class="table-label py-4 text-right">{{ t('leaderboard.score') }}</th><th class="table-label py-4 text-right">{{ t('leaderboard.science') }}</th><th class="table-label py-4 text-right">{{ pick('Program bonus', '项目加成') }}</th><th class="table-label py-4 text-right">{{ pick('Request reward', '请求奖励') }}</th><th class="table-label py-4 text-right">{{ pick('Penalties', '惩罚项') }}</th><th class="table-label py-4 text-right">{{ t('leaderboard.submissions') }}</th></tr></thead>
              <tbody><tr v-for="row in entries" :key="`${row.rank}:${row.name}`" class="border-b border-white/10"><td class="py-4 font-mono text-xs text-[#edb28b]">{{ row.rank }}</td><td class="py-4 text-sm font-medium text-text-primary">{{ row.name }}</td><td class="py-4 text-right font-mono text-sm">{{ metric(row.score) }}</td><td class="py-4 text-right font-mono text-sm">{{ metric(row.science) }}</td><td class="py-4 text-right font-mono text-sm">{{ metric(row.bonus) }}</td><td class="py-4 text-right font-mono text-sm">{{ metric(row.requests) }}</td><td class="py-4 text-right font-mono text-sm">{{ metric(row.penalties) }}</td><td class="py-4 text-right font-mono text-sm">{{ row.submissions }}</td></tr></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.leaderboard-aura { top: 8%; right: -23rem; width: 58rem; opacity: .44; transform: rotate(54deg); }
.signal-dot { width: .55rem; height: .55rem; background: #edb28b; box-shadow: 1rem 0 0 rgba(240,233,221,.8); }
.table-label { color: #8f9ba1; font-family: var(--font-mono); font-size: .875rem; font-weight: 400; letter-spacing: .1em; text-transform: uppercase; }
</style>
