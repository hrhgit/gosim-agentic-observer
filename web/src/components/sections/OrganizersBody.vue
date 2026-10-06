<script setup lang="ts">
// Organizer and sponsor cards, the scientific committee and the Octos support card.
// Shared by the home Organizers section and the /about page.
import { useI18n } from '../../composables/useI18n'
import { appUrl } from '../../composables/api'
import { useOrganizers } from '../../composables/useOrganizers'

const { t } = useI18n()
const { items, committee, logo, photo } = useOrganizers()
</script>

<template>
  <div class="cards cards-3 cards-fit reveal-stagger mt-14">
    <article v-for="item in items" :key="item.name" v-tilt class="card card-lift org-card">
      <span class="label accent">{{ item.role }}</span>
      <img v-if="logo(item.name)" :src="logo(item.name)" :alt="`${item.name} logo`" class="org-logo" loading="lazy">
      <h3 class="mt-3">{{ item.name }}</h3>
      <p>{{ item.desc }}</p>
    </article>
  </div>
  <div id="committee" class="reveal mt-16 scroll-mt-24">
    <div class="rule-b pb-3">
      <span class="label accent">{{ t('home.credibility.committee.kicker') }}</span>
    </div>
    <p class="mt-7 max-w-5xl text-[clamp(1.4rem,3vw,2.6rem)] font-medium leading-[1.3] tracking-[-.025em] text-[#e8edf8]">{{ t('home.credibility.committee.intro') }}</p>
    <div class="reveal-stagger mt-10 grid grid-cols-3 gap-3 sm:grid-cols-4 md:gap-4 lg:grid-cols-7">
      <figure v-for="m in committee" :key="m.photo" v-tilt class="card card-lift committee-card">
        <img :src="photo(m)" :alt="m.name" class="aspect-[3/4] w-full object-cover" loading="lazy" />
        <figcaption class="mt-4">
          <h3>{{ m.name }}</h3>
          <p>{{ m.title }} · {{ m.org }}</p>
        </figcaption>
      </figure>
    </div>
    <a class="octos-card card card-lift mt-6 flex flex-wrap items-center gap-6" href="https://github.com/octos-org/" target="_blank" rel="noopener" :aria-label="t('home.credibility.octos.cta')">
      <img :src="appUrl('media/octos-logo.webp')" :alt="t('home.credibility.octos.logoAlt')" class="h-14 w-auto object-contain md:h-16" loading="lazy" />
      <span class="min-w-0 flex-1">
        <span class="label accent-amber block">{{ t('home.credibility.octos.kicker') }}</span>
        <h3 class="mt-2">{{ t('home.credibility.octos.title') }}</h3>
        <p>{{ t('home.credibility.octos.desc') }}</p>
      </span>
      <span class="label accent whitespace-nowrap">{{ t('home.credibility.octos.cta') }} ↗</span>
    </a>
  </div>
</template>
