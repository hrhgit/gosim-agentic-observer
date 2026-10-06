<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'
import { useScrollReveal } from '../composables/useScrollReveal'
import domeImage from '../assets/images/cosmos-dome.jpg'
import domeImageAvif from '../assets/images/cosmos-dome.avif'
import domeImageWebp from '../assets/images/cosmos-dome.webp'
import { useAuth } from '../stores/auth'
import { useRegistrationOpen } from '../composables/useRegistrationOpen'

useScrollReveal()
const { t, pick } = useI18n()
const { isLoggedIn } = useAuth()
const { registrationOpen } = useRegistrationOpen()

type Section = {
  kicker: string
  title: string
  paragraphs: string[]
  highlight?: string
  duties?: { n: string; title: string; desc: string }[]
  loopTitle?: string
  loop?: { stage: string; items: string[] }[]
  starterCode?: string
  archCode?: string
  formula?: string
  actionCode?: string
  rounds?: { number: string; name: string; dates: string; format: string; challenge: string; participants: string }[]
}

const sections = computed(() => t('vision.sections') as Section[])
const kickerText = (kicker: string) => kicker.replace(/^\d+ \/ /, '')
</script>

<template>
  <main class="poster-canvas min-h-screen pb-24">
    <header class="vision-hero hero-section relative min-h-[720px] overflow-hidden border-b border-white/20">
      <div class="vision-hero-photo" aria-hidden="true">
        <picture>
          <source :srcset="domeImageAvif" type="image/avif">
          <source :srcset="domeImageWebp" type="image/webp">
          <img :src="domeImage" alt="" loading="lazy" width="1536" height="1024">
        </picture>
        <div class="vision-plasma plasma-field"></div>
      </div>
      <div class="vision-hero-overlay" aria-hidden="true"></div>
      <div class="relative z-10 mx-auto flex min-h-[720px] max-w-[1600px] flex-col justify-between px-5 py-12 md:px-10 md:py-20 xl:px-14">
        <div>
          <router-link
            to="/"
            class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-[.1em] text-white/55 transition-colors hover:text-[#315efb]"
          >
            <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M19 12H5m7 7-7-7 7-7"/></svg>
            {{ t('vision.backToHome') }}
          </router-link>
        </div>
        <div>
          <span class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ pick('AGENTIC OBSERVER HACKATHON / PARTICIPANT BRIEFING / 01—08', '智能体巡天黑客松 / 参赛说明 / 01—08') }}</span>
          <h1 class="mt-7 max-w-[14ch] text-balance text-[clamp(3rem,6.5vw,6.75rem)] font-semibold leading-[1.02] tracking-[-.055em] text-[#f5f5f5]">{{ t('vision.pageTitle') }}</h1>
          <p class="mt-8 max-w-2xl text-base font-medium leading-[1.7] text-white/80 md:text-lg">{{ t('vision.pageSubtitle') }}</p>
          <p class="mt-7 max-w-3xl border-t border-white/30 pt-6 text-sm leading-relaxed text-white/65 md:text-base">{{ t('vision.intro') }}</p>
        </div>
      </div>
    </header>

    <article class="mx-auto max-w-[1400px] px-5 pt-12 md:px-10 md:pt-20 xl:px-14">
      <section
        v-for="(section, index) in sections"
        :key="index"
        class="vision-section reveal grid scroll-mt-24 border-t border-white/20 py-16 md:py-24 lg:grid-cols-[12rem_1fr] lg:gap-16"
        :id="`section-${index + 1}`"
      >
        <aside class="mb-10 lg:mb-0">
          <div class="vision-number font-mono text-xs text-[#315efb]">0{{ index + 1 }}</div>
          <span class="mt-8 inline-block font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ kickerText(section.kicker) }}</span>
        </aside>

        <div>
          <h2 class="section-title">{{ section.title }}</h2>

          <div class="mt-10 grid gap-6 text-base leading-relaxed text-text-secondary md:text-lg lg:grid-cols-2">
            <p v-for="(paragraph, pIndex) in section.paragraphs" :key="pIndex" :class="pIndex === 0 ? 'text-text-primary' : ''">{{ paragraph }}</p>
          </div>

          <blockquote v-if="section.highlight" class="paper-sheet relative z-10 mt-10 p-7 text-lg font-medium leading-relaxed text-[#0a0a0b] md:p-10 md:text-2xl">{{ section.highlight }}</blockquote>

          <div v-if="section.duties" class="mt-12 grid border-t border-white/25 sm:grid-cols-2 lg:grid-cols-3">
            <div
              v-for="duty in section.duties"
              :key="duty.n"
              class="border-b border-white/20 p-5 sm:border-r md:p-6"
            >
              <span class="font-mono text-xs tracking-[.1em] text-[#315efb]">{{ duty.n }}</span>
              <h3 class="mt-4 text-xl font-semibold leading-tight tracking-[-.03em] text-text-primary md:text-2xl">{{ duty.title }}</h3>
              <p class="mt-2 text-sm leading-relaxed text-text-secondary">{{ duty.desc }}</p>
            </div>

          </div>

          <div v-if="section.loop" class="mt-12">
            <h3 class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ section.loopTitle }}</h3>
            <div class="mt-6 grid border-y border-white/25 sm:grid-cols-2 lg:grid-cols-5">
            <div
              v-for="stage in section.loop"
              :key="stage.stage"
              class="border-b border-white/20 p-4 last:border-b-0 sm:border-r lg:border-b-0"
            >
              <h4 class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ stage.stage }}</h4>
              <ul class="mt-3 space-y-1 text-sm text-text-secondary">
                <li v-for="item in stage.items" :key="item">{{ item }}</li>
              </ul>
            </div>
            </div>
          </div>

          <div v-if="section.starterCode" class="mt-10"><pre class="code-block" tabindex="0">{{ section.starterCode }}</pre></div>
          <div v-if="section.archCode" class="mt-4"><pre class="code-block" tabindex="0">{{ section.archCode }}</pre></div>
          <div v-if="section.formula" class="mt-10"><pre class="code-block" tabindex="0">{{ section.formula }}</pre></div>
          <div v-if="section.actionCode" class="mt-10"><pre class="code-block" tabindex="0">{{ section.actionCode }}</pre></div>

          <div v-if="section.rounds" class="mt-12 space-y-4">
            <div v-for="round in section.rounds" :key="round.number" class="grid border-t border-white/25 py-7 md:grid-cols-[8rem_1fr] md:gap-8">
              <div>
                <span class="font-mono text-xs text-[#315efb]">{{ round.number }}</span>
                <p class="mt-3 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ round.dates }}<br>{{ round.format }}</p>
              </div>
              <div class="mt-5 md:mt-0">
                <h3 class="text-2xl font-semibold leading-tight tracking-[-.04em] text-text-primary md:text-3xl">{{ round.name }}</h3>
                <div class="mt-5 grid gap-5 text-sm leading-relaxed text-text-secondary md:grid-cols-2 md:text-base">
                  <div><span class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ t('schedule.challengeLabel') }}</span><p class="mt-2">{{ round.challenge }}</p></div>
                  <div><span class="font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ t('schedule.participantsLabel') }}</span><p class="mt-2">{{ round.participants }}</p></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div class="reveal paper-sheet mt-16 p-8 text-center md:p-14">
        <p class="text-2xl font-semibold tracking-[-.045em] text-[#0a0a0b] md:text-4xl">{{ pick('Next: build your observer.', '下一步：构建你的观测员。') }}</p>
        <router-link
          v-if="isLoggedIn"
          to="/dashboard"
          class="relative z-10 mt-8 inline-block bg-[#0a0a0b] px-7 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-[#f5f5f5] transition-colors hover:bg-[#315efb]"
        >{{ t('hero.cta_dashboard') }} →</router-link>
        <router-link
          v-else-if="registrationOpen"
          to="/register"
          class="relative z-10 mt-8 inline-block bg-[#0a0a0b] px-7 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-[#f5f5f5] transition-colors hover:bg-[#315efb]"
        >{{ t('hero.cta_register') }} →</router-link>
        <span v-else class="relative z-10 mt-8 inline-block bg-[#0a0a0b] px-7 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-[#f5f5f5] opacity-60">{{ t('nav.registration_closed') }}</span>
      </div>
    </article>
  </main>
</template>

<style scoped>
.vision-hero { background: #060607; }
.vision-hero-photo { position: absolute; inset: 0; }
.vision-hero-photo > picture { display: block; width: 100%; height: 100%; }
.vision-hero-photo > picture > img { width: 100%; height: 100%; object-fit: cover; object-position: 58% center; filter: grayscale(1) contrast(1.28) brightness(.58); }
.vision-hero-overlay { position: absolute; z-index: 1; inset: 0; background: linear-gradient(90deg, rgba(6,6,7,.94) 0%, rgba(6,6,7,.74) 46%, rgba(6,6,7,.18) 100%), linear-gradient(0deg, rgba(6,6,7,.88), transparent 62%); }
.vision-plasma { top: 18%; right: -6%; transform: rotate(-8deg); }
.vision-section:nth-child(even) .vision-number { color: #315efb; }
@media (max-width: 720px) {
  .vision-hero-photo > picture > img { object-position: 66% center; }
  .vision-hero-overlay { background: linear-gradient(90deg, rgba(6,6,7,.88), rgba(6,6,7,.5)), linear-gradient(0deg, rgba(6,6,7,.9), transparent 60%); }
  .vision-plasma { top: 15%; right: -42%; }
}
</style>
