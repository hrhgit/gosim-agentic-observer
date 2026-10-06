<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
const terms = computed(() => t('glossary.terms') as { term: string; desc: string }[])
const state = computed(() => t('glossary.state') as { field: string; desc: string }[])
</script>

<template>
  <section id="glossary" class="bg-bg-secondary py-24 md:py-36">
    <div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14">
      <div class="reveal max-w-3xl">
        <span class="section-kicker">{{ t('glossary.kicker') }}</span>
        <h2 class="section-title mt-8">{{ t('glossary.title') }}</h2>
        <p class="mt-6 text-sm leading-relaxed text-text-secondary md:text-base">{{ t('glossary.intro') }}</p>
      </div>

      <dl class="reveal reveal-delay-1 mt-14 grid gap-x-10 gap-y-6 md:grid-cols-2 lg:grid-cols-4">
        <div v-for="g in terms" :key="g.term" class="border-t border-border pt-4">
          <dt class="text-sm font-medium text-text-primary md:text-base">{{ g.term }}</dt>
          <dd class="mt-2 text-sm leading-relaxed text-text-secondary">{{ g.desc }}</dd>
        </div>
      </dl>

      <div class="reveal mt-20 grid gap-10 border-t border-border pt-12 lg:grid-cols-[1.15fr_.85fr] lg:gap-16">
        <div>
          <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('glossary.stateTitle') }}</h3>
          <dl class="mt-7 border-t border-border-subtle">
            <div v-for="s in state" :key="s.field" class="grid gap-2 border-b border-border-subtle py-4 md:grid-cols-[.28fr_1fr] md:gap-6">
              <dt class="font-mono text-sm text-accent">{{ s.field }}</dt>
              <dd class="text-sm leading-relaxed text-text-secondary">{{ s.desc }}</dd>
            </div>
          </dl>
        </div>
        <div>
          <pre class="code-block"><code>{{ t('glossary.actionCode') }}</code></pre>
        </div>
      </div>
    </div>
  </section>
</template>
