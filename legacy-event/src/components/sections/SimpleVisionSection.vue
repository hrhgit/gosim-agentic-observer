<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import VisionCarousel from '../VisionCarousel.vue'
import controlRoomImage from '../../assets/images/cosmos-control-room.jpg'
import cosmicWebImage from '../../assets/images/survey-cosmic-web.jpg'
import agentStrategyImage from '../../assets/images/survey-agent-strategy.jpg'

const { t, pick } = useI18n()

// The sky we sample, the human who decides, the agent taking over.
const slides = computed(() => [
  {
    src: cosmicWebImage,
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
    alt: pick('An observatory control room during a survey night', '巡天之夜的观测站控制室'),
    stamp: pick('OBSERVATORY CONTROL / HUMAN IN THE LOOP', '观测站控制 / 人在回路'),
    caption: pick(
      'Today a human leading observer reads the sky state and decides what to point at next — every 900 seconds, all night long.',
      '今天，是人类主值观测员在读取夜空状态、决定下一个指向——每 900 秒一次，整夜不停。',
    ),
  },
  {
    src: agentStrategyImage,
    alt: pick(
      'An agent observer weighing weather, sky tiles and survey progress into an observing plan',
      '观测智能体把天气、天区与巡天进度权衡成一份观测计划',
    ),
    stamp: pick('AGENT OBSERVER / SURVEY STRATEGY', '观测智能体 / 巡天策略'),
    caption: pick(
      'We let the agent take over: the same sky tiles, the same weather and progress, and it proposes the next pointing.',
      '我们让智能体接手：同样的天区、同样的天气与进度，由它给出下一个指向。',
    ),
  },
])
</script>

<template>
  <section id="about" class="poster-section poster-canvas py-24 md:py-40">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="grid gap-12 lg:grid-cols-[.66fr_1.34fr] lg:gap-16">
        <div class="reveal lg:pt-4">
          <span class="poster-kicker mt-14">{{ t('home.vision.kicker') }}</span>
          <h2 class="section-title distressed-type mt-8">{{ t('home.vision.title') }}</h2>
        </div>

        <div class="reveal reveal-delay-1">
          <VisionCarousel :slides="slides" />

          <div class="mt-9 border-t poster-rule pt-8">
            <div class="max-w-3xl space-y-5 text-base leading-relaxed text-text-secondary md:text-lg">
              <p v-for="(paragraph, index) in t('home.vision.paragraphs')" :key="index">{{ paragraph }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
