<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import VisionCarousel from '../VisionCarousel.vue'
import observedUniverseImage from '../../assets/images/survey-observed-universe.jpg'
import reviewOverlayImage from '../../assets/images/survey-review-overlay.jpg'

const { t, pick } = useI18n()
const principles = computed(() => t('scoring.principles') as { term: string; desc: string }[])

// Both are output of our own reference implementation: what a finished run
// leaves behind, and how a run gets read against the full catalogue.
const vizSlides = computed(() => [
  {
    src: observedUniverseImage,
    alt: pick(
      'The mock universe a balanced agent observer ended up sampling',
      '均衡型观测智能体最终采样到的模拟宇宙',
    ),
    caption: pick(
      'What a run actually produces: the mock galaxies and quasars the agent chose to observe. Different policies leave different cosmic structure visible.',
      '一次运行的真实产出：智能体选择观测到的模拟星系与类星体。不同的策略，会留下不同的宇宙结构。',
    ),
  },
  {
    src: reviewOverlayImage,
    alt: pick(
      'Review overlay of the fixed target catalogue against one observed sample',
      '评审叠加图：固定目标星表与一次运行的观测样本',
    ),
    caption: pick(
      'The same run read against the full catalogue: grey is every target there was, colour is what the agent actually reached. The gap is the score.',
      '同一次运行，放回完整星表里看：灰色是本可以观测的全部目标，彩色是智能体真正够到的部分。差距就是分数。',
    ),
  },
])
</script>

<template>
  <section id="scoring" class="poster-section poster-canvas py-24 md:py-40">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-14 lg:grid-cols-[.85fr_1.15fr] lg:gap-24">
        <div class="reveal relative z-10 lg:sticky lg:top-28 lg:self-start">
          <div class="flex items-start justify-between">
            <span class="poster-kicker">{{ t('scoring.kicker') }}</span>
            <span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">03 / 06</span>
          </div>
          <h2 class="section-title distressed-type mt-10">{{ t('scoring.title') }}</h2>
          <p class="mt-7 max-w-xl text-base leading-relaxed text-text-secondary md:text-lg">{{ t('scoring.intro') }}</p>
        </div>

        <div class="relative z-10">
          <pre class="code-block reveal"><code>{{ t('scoring.formula') }}</code></pre>
          <!-- The version tag is bookkeeping for anyone cross-checking the
               platform docs, so it sits on the formula rather than in the prose. -->
          <p class="reveal mt-3 font-mono text-xs tracking-[.08em] text-text-tertiary">{{ t('scoring.formulaVersion') }}</p>
          <p class="reveal mt-6 max-w-2xl text-sm leading-relaxed text-text-secondary md:text-base">{{ t('scoring.note') }}</p>

          <!-- Everyone meets the same disruptions; the score is decided by what
               they do about them. -->
          <div class="poster-card reveal mt-14 py-9 md:py-12">
            <h3 class="max-w-[24ch] text-xl font-semibold leading-tight tracking-[-.03em] text-[#f0e9dd] md:text-2xl">{{ t('scoring.stressTitle') }}</h3>
            <p class="mt-4 max-w-2xl text-sm leading-relaxed text-text-secondary">{{ t('scoring.stress') }}</p>

            <div class="mt-8 grid gap-7 md:grid-cols-2 md:gap-10">
              <div class="border-t border-white/20 pt-5">
                <span class="font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ t('scoring.stressWeak') }}</span>
                <p class="mt-3 text-sm leading-relaxed text-text-secondary">{{ t('scoring.stressWeakDesc') }}</p>
              </div>
              <div class="border-t border-[#edb28b] pt-5">
                <span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('scoring.stressStrong') }}</span>
                <p class="mt-3 text-sm leading-relaxed text-[#f0e9dd]">{{ t('scoring.stressStrongDesc') }}</p>
              </div>
            </div>
          </div>

          <div class="reveal mt-14 border-t poster-rule pt-10">
            <h3 class="text-xl font-semibold tracking-[-.03em] text-[#f0e9dd] md:text-2xl">{{ t('scoring.principlesTitle') }}</h3>
            <dl class="mt-8 grid gap-x-10 gap-y-7 md:grid-cols-2">
              <div v-for="p in principles" :key="p.term" class="border-t border-white/20 pt-4">
                <dt class="text-base font-semibold text-[#f0e9dd]">{{ p.term }}</dt>
                <dd class="mt-2 text-sm leading-relaxed text-text-secondary">{{ p.desc }}</dd>
              </div>
            </dl>
          </div>

          <div class="reveal mt-14 border-t poster-rule pt-10">
            <h3 class="text-xl font-semibold tracking-[-.03em] text-[#f0e9dd] md:text-2xl">{{ t('scoring.vizTitle') }}</h3>
            <VisionCarousel class="mt-7" variant="plate" :slides="vizSlides" />
            <p class="mt-6 font-mono text-xs leading-relaxed tracking-[.03em] text-text-tertiary">{{ t('scoring.vizNote') }}</p>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
