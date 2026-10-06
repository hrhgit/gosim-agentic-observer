<script setup lang="ts">
import { registrationUrl, platformLinks } from '../registration'
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'
import { useScrollReveal } from '../composables/useScrollReveal'
import domeImage from '../assets/images/cosmos-dome.jpg'
import redshiftMapImage from '../assets/images/survey-redshift-map.jpg'
import reviewOverlayImage from '../assets/images/survey-review-overlay.jpg'

// i18n carries the caption and credit; the key picks the bundled asset.
const figureAssets: Record<string, string> = {
  redshiftMap: redshiftMapImage,
  reviewOverlay: reviewOverlayImage,
}

useScrollReveal()
const { t, pick } = useI18n()

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
  figure?: { key: string; alt: string; caption: string; credit?: string; plate?: boolean }
}

const sections = computed(() => t('vision.sections') as Section[])
</script>

<template>
  <main class="poster-canvas min-h-screen pb-24">
    <header class="vision-hero hero-section relative min-h-[720px] overflow-hidden border-b border-white/20">
      <div class="vision-hero-photo" aria-hidden="true">
        <img :src="domeImage" alt="">
        <div class="vision-plasma plasma-field"></div>
      </div>
      <div class="vision-hero-overlay" aria-hidden="true"></div>
      <div class="relative z-10 mx-auto flex min-h-[720px] max-w-[1600px] flex-col justify-between px-5 py-12 md:px-10 md:py-20 xl:px-14">
        <div>
          <router-link
            to="/"
            class="inline-flex items-center gap-2 font-mono text-xs uppercase tracking-[.1em] text-white/55 transition-colors hover:text-[#edb28b]"
          >
            <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M19 12H5m7 7-7-7 7-7"/></svg>
            {{ t('vision.backToHome') }}
          </router-link>
        </div>
        <div>
          <span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ pick('AGENT OBSERVER / PARTICIPANT BRIEFING / 01—08', '巡天智能体 / 参赛说明 / 01—08') }}</span>
          <h1 class="mt-7 max-w-[14ch] text-balance text-[clamp(3rem,6.5vw,6.75rem)] font-semibold leading-[1.02] tracking-[-.055em] text-[#f0e9dd]">{{ t('vision.pageTitle') }}</h1>
          <p class="mt-8 max-w-2xl text-base font-medium leading-[1.7] text-white/80 md:text-lg">{{ t('vision.pageSubtitle') }}</p>
          <p class="mt-7 max-w-3xl border-t border-white/30 pt-6 text-sm leading-relaxed text-white/65 md:text-base">{{ t('vision.intro') }}</p>
        </div>
      </div>
    </header>

    <article class="mx-auto max-w-[1400px] px-5 pt-12 md:px-10 md:pt-20 xl:px-14">
      <div class="mb-8 border border-[#edb28b]/40 p-6 text-sm leading-relaxed text-text-secondary">
        <p>{{ pick('This page explains the observing challenge. Current interfaces, submission formats and scoring follow the competition platform rules and documentation.', '本页介绍巡天挑战的背景与思路；当前接口、提交格式及计分规则以比赛平台的规则和开发文档为准。') }}</p>
        <div class="mt-4 flex flex-wrap gap-6 text-[#edb28b]">
          <a :href="platformLinks.rules" target="_blank" rel="noopener noreferrer">{{ pick('Competition rules', '比赛规则') }} ↗</a>
          <a :href="platformLinks.docs" target="_blank" rel="noopener noreferrer">{{ pick('Development documentation', '开发文档') }} ↗</a>
        </div>
      </div>
      <section
        v-for="(section, index) in sections"
        :key="index"
        class="vision-section reveal grid scroll-mt-24 border-t border-white/20 py-16 md:py-24 lg:grid-cols-[12rem_1fr] lg:gap-16"
        :id="`section-${index + 1}`"
      >
        <aside class="mb-10 lg:mb-0">
          <div class="vision-number font-mono text-xs text-[#edb28b]">0{{ index + 1 }}</div>
          <span class="mt-8 inline-block font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ section.kicker }}</span>
        </aside>

        <div>
          <h2 class="section-title">{{ section.title }}</h2>

          <div class="mt-10 grid gap-6 text-base leading-relaxed text-text-secondary md:text-lg lg:grid-cols-2">
            <p v-for="(paragraph, pIndex) in section.paragraphs" :key="pIndex" :class="pIndex === 0 ? 'text-text-primary' : ''">{{ paragraph }}</p>
          </div>

          <blockquote v-if="section.highlight" class="paper-sheet relative z-10 mt-10 p-7 text-lg font-medium leading-relaxed text-[#101d29] md:p-10 md:text-2xl">{{ section.highlight }}</blockquote>

          <figure
            v-if="section.figure"
            class="survey-figure mt-12"
            :class="section.figure.plate ? 'survey-figure--plate' : 'survey-figure--sky'"
          >
            <div v-if="section.figure.plate" class="survey-plate paper-sheet">
              <img :src="figureAssets[section.figure.key]" :alt="section.figure.alt" loading="lazy">
            </div>
            <img v-else :src="figureAssets[section.figure.key]" :alt="section.figure.alt" loading="lazy">
            <figcaption>
              {{ section.figure.caption }}
              <span v-if="section.figure.credit" class="figure-credit">{{ section.figure.credit }}</span>
            </figcaption>
          </figure>

          <div v-if="section.duties" class="mt-12 grid border-t border-white/25 sm:grid-cols-2 lg:grid-cols-3">
            <div
              v-for="duty in section.duties"
              :key="duty.n"
              class="border-b border-white/20 p-5 sm:border-r md:p-6"
            >
              <span class="font-mono text-xs tracking-[.1em] text-[#edb28b]">{{ duty.n }}</span>
              <h3 class="mt-4 text-xl font-semibold leading-tight tracking-[-.03em] text-text-primary md:text-2xl">{{ duty.title }}</h3>
              <p class="mt-2 text-sm leading-relaxed text-text-secondary">{{ duty.desc }}</p>
            </div>

          </div>

          <div v-if="section.loop" class="mt-12">
            <h3 class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ section.loopTitle }}</h3>
            <div class="mt-6 grid border-y border-white/25 sm:grid-cols-2 lg:grid-cols-5">
            <div
              v-for="stage in section.loop"
              :key="stage.stage"
              class="border-b border-white/20 p-4 last:border-b-0 sm:border-r lg:border-b-0"
            >
              <h4 class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ stage.stage }}</h4>
              <ul class="mt-3 space-y-1 text-sm text-text-secondary">
                <li v-for="item in stage.items" :key="item">{{ item }}</li>
              </ul>
            </div>
            </div>
          </div>

          <div v-if="section.starterCode" class="mt-10"><pre class="code-block">{{ section.starterCode }}</pre></div>
          <div v-if="section.archCode" class="mt-4"><pre class="code-block">{{ section.archCode }}</pre></div>
          <div v-if="section.formula" class="mt-10"><pre class="code-block">{{ section.formula }}</pre></div>
          <div v-if="section.actionCode" class="mt-10"><pre class="code-block">{{ section.actionCode }}</pre></div>

          <div v-if="section.rounds" class="mt-12 space-y-4">
            <div v-for="round in section.rounds" :key="round.number" class="grid border-t border-white/25 py-7 md:grid-cols-[8rem_1fr] md:gap-8">
              <div>
                <span class="font-mono text-xs text-[#edb28b]">{{ round.number }}</span>
                <p class="mt-3 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ round.dates }}<br>{{ round.format }}</p>
              </div>
              <div class="mt-5 md:mt-0">
                <h3 class="text-2xl font-semibold leading-tight tracking-[-.04em] text-text-primary md:text-3xl">{{ round.name }}</h3>
                <div class="mt-5 grid gap-5 text-sm leading-relaxed text-text-secondary md:grid-cols-2 md:text-base">
                  <div><span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('schedule.challengeLabel') }}</span><p class="mt-2">{{ round.challenge }}</p></div>
                  <div><span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('schedule.participantsLabel') }}</span><p class="mt-2">{{ round.participants }}</p></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <div class="reveal paper-sheet mt-16 p-8 text-center md:p-14">
        <p class="text-2xl font-semibold tracking-[-.045em] text-[#101d29] md:text-4xl">{{ pick('Ready to build the next observer?', '准备好构建下一代观测员了吗？') }}</p>
        <a :href="registrationUrl" target="_blank" rel="noopener noreferrer"
          class="relative z-10 mt-8 inline-block bg-[#101d29] px-7 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-[#f0e9dd]"
        >
          {{ pick('Register now', '立即报名') }}
        </a>
      </div>
    </article>
  </main>
</template>

<style scoped>
.vision-hero { background: #18242f; }
.vision-hero-photo { position: absolute; inset: 0; }
.vision-hero-photo > img { width: 100%; height: 100%; object-fit: cover; object-position: 58% center; filter: grayscale(1) contrast(1.28) brightness(.58); }
.vision-hero-overlay { position: absolute; z-index: 1; inset: 0; background: linear-gradient(90deg, rgba(24,36,47,.94) 0%, rgba(24,36,47,.74) 46%, rgba(24,36,47,.18) 100%), linear-gradient(0deg, rgba(24,36,47,.88), transparent 62%); }
.vision-plasma { top: 18%; right: -6%; transform: rotate(-8deg); }
.vision-section:nth-child(even) .vision-number { color: #edb28b; }
@media (max-width: 720px) {
  .vision-hero-photo > img { object-position: 66% center; }
  .vision-hero-overlay { background: linear-gradient(90deg, rgba(24,36,47,.88), rgba(24,36,47,.5)), linear-gradient(0deg, rgba(24,36,47,.9), transparent 60%); }
  .vision-plasma { top: 15%; right: -42%; }
}
</style>
