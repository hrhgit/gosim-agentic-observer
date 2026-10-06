<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
type Criterion = { term: string; desc: string }
const criteria = computed(() => t('home.evaluation.criteria') as Criterion[])
</script>

<template>
  <section id="evaluation" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.85fr_1.15fr] lg:gap-20">
        <div class="reveal">
          <span class="poster-kicker">{{ t('home.evaluation.kicker') }}</span>
          <h2 class="section-title distressed-type mt-8">{{ t('home.evaluation.title') }}</h2>
          <p class="mt-7 max-w-xl text-base leading-relaxed text-text-secondary md:text-lg">{{ t('home.evaluation.lede') }}</p>
          <p class="mt-8"><router-link to="/rules" class="label accent">{{ t('home.evaluation.link') }} →</router-link></p>
        </div>
        <div class="reveal reveal-delay-1 min-w-0">
          <pre class="code-block" tabindex="0">{{ t('home.evaluation.formula') }}</pre>
          <dl class="dl dl-2 reveal-stagger mt-10">
            <div v-for="item in criteria" :key="item.term"><dt>{{ item.term }}</dt><dd>{{ item.desc }}</dd></div>
          </dl>
        </div>
      </div>
    </div>
  </section>
</template>
