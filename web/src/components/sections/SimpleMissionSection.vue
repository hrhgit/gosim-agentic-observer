<script setup lang="ts">
import { competition } from '../../stores/competition'
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import instrumentImage from '../../assets/images/cosmos-instrument.jpg'
import instrumentAvif from '../../assets/images/cosmos-instrument.avif'
import instrumentWebp from '../../assets/images/cosmos-instrument.webp'

const { t, pick } = useI18n()
type Card = { title: string; desc: string }
const cards = computed(() => t('home.mission.cards') as Card[])
const cardItems = computed(() => t('home.mission.cardItems') as { term: string; desc: string }[])
</script>

<template>
  <section id="mission" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mission-aura plasma-field" aria-hidden="true"></div>
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.85fr_1.15fr] lg:gap-24">
        <div class="reveal relative z-10 lg:sticky lg:top-28 lg:self-start">
          <div class="flex items-start justify-between">
            <span class="poster-kicker">{{ t('home.mission.kicker') }}</span>
            <span class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">02 / 04</span>
          </div>
          <h2 class="section-title distressed-type mt-10">{{ t('home.mission.title') }}</h2>
          <p class="mt-7 max-w-xl text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.mission.lede') }}</p>

          <div class="reveal mt-14 paper-sheet p-7 md:p-10">
            <span class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ pick('Getting started', '开始参与') }}</span>
            <p class="relative z-10 mt-5 max-w-[24ch] text-xl font-semibold leading-relaxed tracking-[-.02em] md:text-2xl">{{ t('home.mission.closing') }}</p>
          </div>

        </div>

        <div class="relative z-10">
          <div class="mission-photo photo-wash reveal mb-12 h-[300px] md:h-[440px]">
            <picture>
              <source :srcset="instrumentAvif" type="image/avif">
              <source :srcset="instrumentWebp" type="image/webp">
              <img :src="instrumentImage" alt="" loading="lazy" width="1881" height="836">
            </picture>
            <span>{{ pick('INSTRUMENT CALIBRATION / HUMAN OVERSIGHT', '仪器标定 / 人类监督') }}</span>
          </div>
          <article v-for="(card, index) in cards" :key="card.title" class="poster-card row-sweep reveal py-9 pl-3 md:grid md:grid-cols-[5rem_1fr] md:gap-8 md:py-12" :class="`reveal-delay-${index + 1}`">
            <span class="font-mono text-xs text-[#315efb]">0{{ index + 1 }}</span>
            <div class="mt-5 md:mt-0">
              <h3 class="max-w-[18ch] text-xl font-semibold leading-tight tracking-[-.03em] text-[#f5f5f5] md:text-2xl">{{ card.title }}</h3>
              <p class="mt-4 max-w-xl text-sm leading-relaxed text-text-secondary">{{ card.desc }}</p>
            </div>
          </article>

          <div v-tilt class="reveal mission-sheet mt-12 paper-sheet p-7 md:p-10">
            <div class="relative z-10 flex flex-wrap items-center justify-between gap-3">
              <span class="font-mono text-xs uppercase tracking-[.1em] text-[#9c5c38]">{{ t('home.mission.cardKicker') }}</span>
              <span class="mission-serial">SMC · 2026 · {{ competition.mode==='practice' ? 'PRACTICE' : 'COMPETITION' }}</span>
            </div>
            <h3 class="relative z-10 mt-4 text-2xl font-semibold tracking-[-.03em] md:text-3xl">{{ t('home.mission.cardTitle') }}</h3>
            <p class="relative z-10 mt-4 max-w-2xl text-sm leading-relaxed text-[#101d29]/75">{{ t('home.mission.cardLede') }}</p>
            <dl class="relative z-10 mt-8 grid gap-x-10 gap-y-5 sm:grid-cols-2">
              <div v-for="(item, idx) in cardItems" :key="item.term" class="border-t border-[#101d29]/20 pt-3">
                <dt class="text-sm font-semibold"><i class="mission-idx">{{ (idx + 1).toString().padStart(2, '0') }}</i>{{ item.term }}</dt>
                <dd class="mt-1 text-sm leading-relaxed text-[#101d29]/70">{{ item.desc }}</dd>
              </div>
            </dl>
            <p class="relative z-10 mt-8 border-t border-[#101d29]/20 pt-5 text-xs leading-relaxed text-[#101d29]/65">{{ t('home.mission.cardNote') }}</p>
            <div class="mission-punch relative z-10 mt-6" aria-hidden="true"></div>
          </div>

        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.mission-aura { top: 35%; left: -26rem; width: 55rem; opacity: .38; transform: rotate(72deg); }
.mission-photo { box-shadow: 8px 8px 0 #315efb; }
.mission-photo img { object-position: 58% center; }
.mission-photo::after { background: linear-gradient(90deg, rgba(7,7,8,.48), transparent 48%), linear-gradient(0deg, rgba(7,7,8,.65), transparent 45%); }
.mission-photo span { position: absolute; z-index: 3; right: 1rem; bottom: 1rem; color: rgba(255,255,255,.72); font-family: 'IBM Plex Mono', monospace; font-size: .875rem; letter-spacing: .1em; }
.mission-900 { position: relative; }
.mission-900::after {
  position: absolute;
  animation: mission-ring 6s ease-in-out infinite;
  top: 27%;
  right: 4%;
  width: 7rem;
  height: 7rem;
  border: 1px solid rgba(49,94,251,.6);
  border-radius: 50% !important;
  content: '';
  box-shadow: 0 0 0 1.4rem rgba(49,94,251,.1), 0 0 0 2.8rem rgba(49,94,251,.05);
}
@keyframes mission-ring {
  0%, 100% { box-shadow: 0 0 0 1.4rem rgba(49,94,251,.1), 0 0 0 2.8rem rgba(49,94,251,.05); }
  50% { box-shadow: 0 0 0 1.9rem rgba(49,94,251,.14), 0 0 0 3.6rem rgba(49,94,251,.03); }
}
@media (prefers-reduced-motion: reduce) { .mission-900::after { animation: none; } }
</style>
