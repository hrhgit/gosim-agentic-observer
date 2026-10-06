<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
type Duty = { n: string; title: string; desc: string }
const duties = computed(() => t('observer.duties') as Duty[])
</script>

<template>
  <section id="observer" class="bg-bg-secondary py-24 md:py-36">
    <div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14">
      <div class="reveal max-w-3xl">
        <span class="section-kicker">{{ t('observer.kicker') }}</span>
        <h2 class="section-title mt-8">{{ t('observer.title') }}</h2>
        <p class="mt-6 text-sm leading-relaxed text-text-secondary md:text-base">{{ t('observer.intro') }}</p>
      </div>

      <div class="reveal reveal-delay-1 mt-14 grid gap-px border border-border bg-border md:grid-cols-2 lg:grid-cols-3">
        <article v-for="d in duties" :key="d.n" class="bg-bg-secondary p-7">
          <span class="font-mono text-[11px] tracking-[.1em] text-accent">{{ d.n }}</span>
          <h3 class="mt-3 text-base font-medium text-text-primary md:text-lg">{{ d.title }}</h3>
          <p class="mt-2 text-sm leading-relaxed text-text-secondary">{{ d.desc }}</p>
        </article>
      </div>
    </div>
  </section>
</template>
