<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import MissionPanel from '../MissionPanel.vue'

const { t } = useI18n()
type Card = { title: string; desc: string }
const cards = computed(() => t('home.mission.cards') as Card[])
const cardItems = computed(() => t('home.mission.cardItems') as { term: string; desc: string }[])
</script>

<template>
  <section id="mission" class="poster-section poster-canvas py-24 md:py-40">
    <div class="mission-aura plasma-field" aria-hidden="true"></div>
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.85fr_1.15fr] lg:gap-24">
        <div class="reveal relative z-10 lg:sticky lg:top-28 lg:self-start">
          <div class="flex items-start justify-between">
            <span class="poster-kicker">{{ t('home.mission.kicker') }}</span>
            <span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">02 / 06</span>
          </div>
          <h2 class="section-title distressed-type mt-10">{{ t('home.mission.title') }}</h2>
          <p class="mt-7 max-w-xl text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.mission.lede') }}</p>

        </div>

        <div class="relative z-10">
          <!-- The card is what the challenge arrives as, so it arrives here as
               a card too. -->
          <div class="reveal paper-sheet p-7 md:p-10">
            <span class="relative z-10 font-mono text-xs uppercase tracking-[.1em] text-[#9c5c38]">{{ t('home.mission.cardKicker') }}</span>
            <h3 class="relative z-10 mt-4 text-2xl font-semibold tracking-[-.03em] md:text-3xl">{{ t('home.mission.cardTitle') }}</h3>
            <p class="relative z-10 mt-4 max-w-2xl text-sm leading-relaxed text-[#101d29]/75">{{ t('home.mission.cardLede') }}</p>

            <dl class="relative z-10 mt-8 grid gap-x-10 gap-y-5 sm:grid-cols-2">
              <div v-for="item in cardItems" :key="item.term" class="border-t border-[#101d29]/20 pt-3">
                <dt class="text-sm font-semibold">{{ item.term }}</dt>
                <dd class="mt-1 text-sm leading-relaxed text-[#101d29]/70">{{ item.desc }}</dd>
              </div>
            </dl>

            <p class="relative z-10 mt-8 border-t border-[#101d29]/20 pt-5 text-xs leading-relaxed text-[#101d29]/65">{{ t('home.mission.cardNote') }}</p>
          </div>

          <!-- One decision in three beats: the same patch of sky, known better
               and with fewer options open, each step down the page. -->
          <article
            v-for="(card, index) in cards" :key="card.title"
            class="poster-card reveal py-9 md:grid md:grid-cols-[5rem_1fr_17rem] md:items-start md:gap-8 md:py-12"
            :class="[`reveal-delay-${index + 1}`, index === 0 ? 'mt-14' : '']"
          >
            <span class="font-mono text-xs text-[#edb28b]">0{{ index + 1 }}</span>
            <div class="mt-5 md:mt-0">
              <h3 class="max-w-[18ch] text-xl font-semibold leading-tight tracking-[-.03em] text-[#f0e9dd] md:text-2xl">{{ card.title }}</h3>
              <p class="mt-4 max-w-xl text-sm leading-relaxed text-text-secondary">{{ card.desc }}</p>
            </div>
            <MissionPanel class="mt-7 md:mt-0" :panel="(index + 1) as 1 | 2 | 3" />
          </article>

        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.mission-aura { top: 35%; left: -26rem; width: 55rem; opacity: .38; transform: rotate(72deg); }
</style>
