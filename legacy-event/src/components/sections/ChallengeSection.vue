<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
const cards = computed(() => t('challenge.cards') as { title: string; desc: string }[])
const loop = computed(() => t('challenge.loop') as { stage: string; items: string[] }[])
</script>

<template>
  <section id="challenge" class="bg-bg-primary py-24 md:py-36">
    <div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14">
      <div class="reveal max-w-3xl">
        <span class="section-kicker">{{ t('challenge.kicker') }}</span>
        <h2 class="section-title mt-8">{{ t('challenge.title') }}</h2>
        <p class="mt-6 text-sm leading-relaxed text-text-secondary md:text-base">{{ t('challenge.intro') }}</p>
      </div>

      <div class="reveal reveal-delay-1 mt-14 grid gap-px border border-border bg-border md:grid-cols-2 lg:grid-cols-3">
        <article v-for="c in cards" :key="c.title" class="bg-bg-primary p-7">
          <h3 class="text-base font-medium text-text-primary md:text-lg">{{ c.title }}</h3>
          <p class="mt-2 text-sm leading-relaxed text-text-secondary">{{ c.desc }}</p>
        </article>
      </div>

      <div class="reveal mt-20 border-t border-border pt-12">
        <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('challenge.loopTitle') }}</h3>
        <div class="mt-8 flex flex-wrap items-stretch gap-x-4 gap-y-6">
          <template v-for="(step, i) in loop" :key="step.stage">
            <div class="min-w-[9rem] flex-1 border border-border bg-bg-card p-5">
              <span class="mono-label text-accent">{{ step.stage }}</span>
              <ul class="mt-3 space-y-1">
                <li v-for="it in step.items" :key="it" class="text-sm text-text-secondary">{{ it }}</li>
              </ul>
            </div>
            <span v-if="i < loop.length - 1" class="self-center text-text-muted">→</span>
          </template>
        </div>
        <p class="mt-8 max-w-4xl text-sm leading-relaxed text-text-secondary md:text-base">{{ t('challenge.loopNote') }}</p>
      </div>
    </div>
  </section>
</template>
