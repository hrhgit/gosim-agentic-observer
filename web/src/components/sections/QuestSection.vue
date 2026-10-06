<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'

const { t } = useI18n()
const { isLoggedIn, me } = useAuth()

type Level = { title: string; desc: string; time: string; to: string }
const levels = computed(() => t('home.quest.levels') as Level[])

// The furthest step the site can verify: registration, then a team. Local runs
// and uploads happen off-site, so the trail hands over to the dashboard there.
const current = computed(() => (!isLoggedIn.value ? 0 : !me.value?.team ? 1 : 2))
</script>

<template>
  <section id="quest" class="poster-section poster-canvas py-16 md:py-24" data-testid="quest-strip">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="reveal flex flex-wrap items-baseline justify-between gap-3">
        <span class="poster-kicker kicker-amber">{{ t('home.quest.kicker') }}</span>
        <span class="label">{{ t('home.quest.note') }}</span>
      </div>
      <div class="quest-strip reveal-stagger mt-8">
        <router-link
          v-for="(level, i) in levels"
          :key="level.title"
          :to="level.to"
          v-tilt
          class="quest-card"
          :class="{ current: i === current, done: i < current }"
        >
          <span class="quest-step">STEP 0{{ i + 1 }}</span>
          <span class="quest-time">{{ level.time }}</span>
          <h3>{{ level.title }}</h3>
          <p>{{ level.desc }}</p>
          <span class="quest-chip">{{ i < current ? t('home.quest.done_chip') : i === current ? '▶ ' + t('home.quest.current_chip') : t('home.quest.locked_chip') }}</span>
        </router-link>
      </div>
    </div>
  </section>
</template>
