<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
const io = computed(() => t('build.io') as { term: string; desc: string }[])
const architectures = computed(() => t('build.architectures') as { name: string; desc: string }[])
const scenarios = computed(() => t('build.scenarios') as { when: string; then: string }[])
</script>

<template>
  <section id="build" class="bg-bg-secondary py-24 md:py-36">
    <div class="mx-auto max-w-[1440px] px-6 md:px-10 xl:px-14">
      <div class="grid gap-10 lg:grid-cols-[.85fr_1.15fr] lg:gap-20">
        <div class="reveal lg:sticky lg:top-28 lg:self-start">
          <span class="section-kicker">{{ t('build.kicker') }}</span>
          <h2 class="section-title mt-8">{{ t('build.title') }}</h2>
          <p class="mt-6 text-sm leading-relaxed text-text-secondary md:text-base">{{ t('build.intro') }}</p>
        </div>

        <div class="reveal reveal-delay-1">
          <dl class="grid gap-x-10 gap-y-6 sm:grid-cols-2">
            <div v-for="x in io" :key="x.term" class="border-t border-border pt-4">
              <dt class="mono-label text-accent">{{ x.term }}</dt>
              <dd class="mt-2 text-sm leading-relaxed text-text-secondary">{{ x.desc }}</dd>
            </div>
          </dl>
        </div>
      </div>

      <div class="reveal mt-20 grid gap-10 border-t border-border pt-12 lg:grid-cols-2 lg:gap-16">
        <div>
          <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('build.starterTitle') }}</h3>
          <p class="mt-4 text-sm leading-relaxed text-text-secondary md:text-base">{{ t('build.starterIntro') }}</p>
          <pre class="code-block mt-6"><code>{{ t('build.starterCode') }}</code></pre>
          <p class="mt-5 text-sm leading-relaxed text-text-secondary">{{ t('build.starterClosing') }}</p>
        </div>
        <div>
          <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('build.archTitle') }}</h3>
          <ul class="mt-6 space-y-4">
            <li v-for="a in architectures" :key="a.name" class="border-l-2 border-border pl-4">
              <span class="text-sm font-medium text-text-primary md:text-base">{{ a.name }}</span>
              <p class="mt-1 text-sm leading-relaxed text-text-secondary">{{ a.desc }}</p>
            </li>
          </ul>
          <pre class="code-block mt-6"><code>{{ t('build.archCode') }}</code></pre>
        </div>
      </div>

      <div class="reveal mt-20 border-t border-border pt-12">
        <h3 class="text-xl font-semibold tracking-[-.03em] text-text-primary md:text-2xl">{{ t('build.scenariosTitle') }}</h3>
        <p class="mt-4 max-w-3xl text-sm leading-relaxed text-text-secondary md:text-base">{{ t('build.scenariosIntro') }}</p>
        <div class="mt-8 border-t border-border">
          <div v-for="s in scenarios" :key="s.when" class="grid gap-2 border-b border-border-subtle py-5 md:grid-cols-[.3fr_1fr] md:gap-8">
            <span class="text-sm font-medium text-text-primary md:text-base">{{ s.when }}</span>
            <span class="text-sm leading-relaxed text-text-secondary">{{ s.then }}</span>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
