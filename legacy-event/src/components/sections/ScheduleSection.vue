<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
const rounds = computed(() => t('schedule.rounds') as any[])
</script>
<template>
  <section id="schedule" class="bg-bg-secondary py-24 md:py-36"><div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14"><div class="grid gap-10 lg:grid-cols-[.7fr_1.3fr] lg:gap-20">
    <div class="reveal"><span class="section-kicker">{{ t('schedule.kicker') }}</span><h2 class="section-title mt-8">{{ t('schedule.title') }}</h2><p class="mt-8 max-w-lg leading-relaxed text-text-secondary">{{ t('schedule.intro') }}</p></div>
    <div class="reveal reveal-delay-1 border-t border-border"><article v-for="round in rounds" :key="round.number" class="grid gap-5 border-b border-border py-8 md:grid-cols-[4rem_.72fr_1.28fr] md:gap-6 md:py-10"><span class="font-mono text-xs text-accent">{{ round.number }}</span><div><p class="mono-label text-text-muted">{{ round.dates }}</p><h3 class="mt-4 text-2xl font-semibold leading-tight tracking-[-0.04em] text-text-primary md:text-3xl">{{ round.name }}</h3><p class="mt-3 text-sm text-text-secondary">{{ round.format }}</p></div><dl class="grid gap-5 text-sm"><div><dt class="mono-label text-text-muted">{{ t('schedule.challengeLabel') }}</dt><dd class="mt-2 leading-relaxed text-text-primary">{{ round.challenge }}</dd></div><div><dt class="mono-label text-text-muted">{{ t('schedule.participantsLabel') }}</dt><dd class="mt-2 leading-relaxed text-text-secondary">{{ round.participants }}</dd></div></dl></article></div>
  </div></div></section>
</template>
