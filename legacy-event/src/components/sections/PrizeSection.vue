<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'

const { t, pick } = useI18n()
type Prize = { place: string; amount: string; count: string }
const prizes = computed(() => t('home.prizes.tiers') as Prize[])
</script>

<template>
  <section id="prizes" class="poster-section poster-canvas py-24 md:py-40">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="reveal grid gap-14 border-y poster-rule py-14 lg:grid-cols-[.65fr_1.35fr] lg:gap-20 lg:py-20">
        <div>
          <span class="mt-8 inline-block font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('home.prizes.kicker') }}</span>
          <h2 class="section-title distressed-type mt-8">{{ t('home.prizes.title') }}</h2>
          <p class="mt-7 max-w-lg text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.prizes.lede') }}</p>
        </div>

        <div>
          <article v-for="(prize, index) in prizes" :key="prize.place" class="grid grid-cols-[2.5rem_1fr_auto] items-center gap-4 border-t border-white/25 py-7 md:grid-cols-[4rem_1fr_auto] md:py-9" :class="index === prizes.length - 1 ? 'border-b' : ''">
            <span class="font-mono text-xs text-[#edb28b]">0{{ index + 1 }}</span>
            <div>
              <h3 class="text-lg font-semibold tracking-[-.025em] text-text-primary md:text-2xl">{{ prize.place }}</h3>
              <p class="mt-2 font-mono text-xs uppercase tracking-[.09em] text-text-tertiary">{{ prize.count }}</p>
            </div>
            <strong class="text-2xl font-semibold tracking-[-.04em] text-[#f0e9dd] md:text-4xl">{{ prize.amount }}</strong>
          </article>
        </div>
      </div>

      <div class="reveal mt-8 flex justify-between gap-6 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">
        <span>{{ pick('6 awards total', '共 6 个奖项') }}</span>
        <span>{{ pick('Awards · Oct 17 · Shenzhen', '10 月 17 日 · 深圳颁奖') }}</span>
      </div>
    </div>
  </section>
</template>
