<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
type Item = { term: string; desc: string }
const hardItems = computed(() => t('about.hardItems') as Item[])
const split = computed(() => t('about.split') as { side: string; desc: string }[])
</script>

<template>
  <section id="about" class="bg-bg-primary py-24 md:py-36">
    <div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14">
      <div class="grid gap-10 lg:grid-cols-[.85fr_1.15fr] lg:gap-20">
        <div class="reveal lg:sticky lg:top-28 lg:self-start">
          <span class="section-kicker">{{ t('about.kicker') }}</span>
          <h2 class="section-title mt-8">{{ t('about.title') }}</h2>
        </div>
        <div class="reveal reveal-delay-1 space-y-6 text-sm leading-relaxed text-text-secondary md:text-base">
          <p>{{ t('about.p1') }}</p>
          <p>{{ t('about.p2') }}</p>
          <p>{{ t('about.p3') }}</p>
        </div>
      </div>

      <div class="reveal mt-20 border-t border-border pt-12">
        <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('about.hardTitle') }}</h3>
        <dl class="mt-8 grid gap-x-10 gap-y-7 md:grid-cols-2 lg:grid-cols-4">
          <div v-for="item in hardItems" :key="item.term" class="border-t border-border-subtle pt-4">
            <dt class="text-base font-medium text-text-primary">{{ item.term }}</dt>
            <dd class="mt-2 text-sm leading-relaxed text-text-secondary">{{ item.desc }}</dd>
          </div>
        </dl>
        <p class="mt-8 max-w-4xl text-sm leading-relaxed text-text-secondary md:text-base">{{ t('about.hardClosing') }}</p>
      </div>

      <div class="reveal mt-16 border-t border-border pt-12">
        <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('about.whyNowTitle') }}</h3>
        <p class="mt-6 max-w-4xl text-sm leading-relaxed text-text-secondary md:text-base">{{ t('about.whyNow') }}</p>
        <p class="mt-6 max-w-4xl text-lg font-medium leading-relaxed text-text-primary md:text-2xl">{{ t('about.whyNowClosing') }}</p>

        <div class="mt-10 grid gap-px overflow-hidden border border-border bg-border md:grid-cols-2">
          <div v-for="s in split" :key="s.side" class="bg-bg-primary p-7">
            <span class="mono-label text-accent">{{ s.side }}</span>
            <p class="mt-3 text-sm leading-relaxed text-text-secondary md:text-base">{{ s.desc }}</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
