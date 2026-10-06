<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import VisionCarousel from '../VisionCarousel.vue'
import controlRoomImage from '../../assets/images/cosmos-control-room.jpg'
import controlRoomAvif from '../../assets/images/cosmos-control-room.avif'
import controlRoomWebp from '../../assets/images/cosmos-control-room.webp'
import cosmicWebImage from '../../assets/images/survey-cosmic-web.jpg'
import cosmicWebAvif from '../../assets/images/survey-cosmic-web.avif'
import cosmicWebWebp from '../../assets/images/survey-cosmic-web.webp'
import agentStrategyImage from '../../assets/images/survey-agent-strategy.jpg'
import agentStrategyAvif from '../../assets/images/survey-agent-strategy.avif'
import agentStrategyWebp from '../../assets/images/survey-agent-strategy.webp'

const { t, pick } = useI18n()

// The sky we sample, the human who decides, the agent taking over.
const slides = computed(() => [
  {
    src: cosmicWebImage,
    avif: cosmicWebAvif,
    webp: cosmicWebWebp,
    alt: pick('Galaxies tracing the cosmic web', '勾勒出宇宙网的星系分布'),
    stamp: pick('COSMIC WEB / WIDE-FIELD SAMPLING', '宇宙网 / 广域巡天采样'),
    caption: pick(
      'Wide-field surveys are ultimately sampling the cosmic web: galaxies trace the large-scale structure shaped by dark matter.',
      '广域巡天归根结底是在对宇宙网采样：星系勾勒出由暗物质塑造的大尺度结构。',
    ),
    credit: 'NASA Science / Hubble · Probing the Cosmic Web',
  },
  {
    src: controlRoomImage,
    avif: controlRoomAvif,
    webp: controlRoomWebp,
    alt: pick('An observatory control room during a survey night', '巡天之夜的观测站控制室'),
    stamp: pick('OBSERVATORY CONTROL / HUMAN IN THE LOOP', '观测站控制 / 人在回路'),
    caption: pick(
      'Today a human leading observer reads the sky state and decides what to point at next — every 900 seconds, all night long.',
      '今天，是人类主值观测员在读取夜空状态、决定下一个指向——每 900 秒一次，整夜不停。',
    ),
  },
  {
    src: agentStrategyImage,
    avif: agentStrategyAvif,
    webp: agentStrategyWebp,
    alt: pick(
      'An agent observer weighing weather, sky tiles and survey progress into an observing plan',
      '观测智能体把天气、天区与巡天进度权衡成一份观测计划',
    ),
    stamp: pick('AGENTIC OBSERVER / SURVEY STRATEGY', '观测智能体 / 巡天策略'),
    caption: pick(
      'We let the agent take over: the same sky tiles, the same weather and progress, and it proposes the next pointing.',
      '我们让智能体接手：同样的天区、同样的天气与进度，由它给出下一个指向。',
    ),
  },
])
type Stat = { value: string; label: string }
const stats = computed(() => { const value = t('home.vision.stats'); return Array.isArray(value) ? (value as Stat[]) : [] })
</script>

<template>
  <section id="about" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-12 lg:grid-cols-[.78fr_1.22fr] lg:gap-16">
        <div class="reveal lg:sticky lg:top-28 lg:self-start lg:pt-4">
          <span class="poster-kicker mt-14">{{ t('home.vision.kicker') }}</span>
          <h2 class="section-title distressed-type mt-8">{{ t('home.vision.title') }}</h2>
        </div>

        <div class="reveal reveal-delay-1">
          <VisionCarousel :slides="slides" />

          <div class="mt-9 grid gap-8 border-t poster-rule pt-8 md:grid-cols-[.72fr_1.28fr]">
            <p class="max-w-3xl text-xl font-medium leading-relaxed tracking-[-.02em] text-[#f5f5f5] md:text-3xl">{{ t('home.vision.lede') }}</p>
            <div>
              <div class="space-y-5 text-base leading-relaxed text-text-secondary md:text-lg">
                <p v-for="(paragraph, index) in t('home.vision.paragraphs')" :key="index">{{ paragraph }}</p>
              </div>
              <router-link to="/brief" class="mt-8 inline-flex items-center gap-3 border-b border-[#315efb] pb-2 font-mono text-xs uppercase tracking-[.1em] text-[#f5f5f5] transition-colors hover:text-[#315efb]">
                {{ t('home.vision.link') }} <span>↗</span>
              </router-link>
            </div>
          </div>
        </div>
      </div>

      <div class="reveal-stagger mt-20 grid border-y poster-rule md:grid-cols-3">
        <div v-for="(stat, index) in stats" :key="stat.label" class="grid grid-cols-[auto_1fr] items-end gap-5 border-b poster-rule px-2 py-8 last:border-b-0 md:border-r md:border-b-0 md:px-8 md:first:pl-0 md:last:border-r-0">
          <span class="text-3xl font-semibold tracking-[-.04em] text-[#315efb] md:text-4xl">{{ stat.value }}</span>
          <span class="mb-2 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ stat.label }}</span>
          <span class="hidden text-right font-mono text-xs text-[#315efb] md:block">0{{ index + 1 }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.vision-photo { transform: rotate(-.2deg); box-shadow: 8px 8px 0 #315efb; }
.vision-photo img { object-position: center 45%; }
.vision-photo::after { background: linear-gradient(90deg, rgba(7,7,8,.46), transparent 48%), linear-gradient(0deg, rgba(7,7,8,.7), transparent 45%); }
.vision-plasma { right: -10%; bottom: -12%; width: min(45rem, 65vw); opacity: .88; transform: rotate(5deg); }
.vision-stamp {
  position: absolute;
  z-index: 4;
  right: 1.25rem;
  bottom: 1.1rem;
  color: rgba(255,255,255,.7);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .875rem;
  letter-spacing: .16em;
  text-transform: uppercase;
}
</style>
