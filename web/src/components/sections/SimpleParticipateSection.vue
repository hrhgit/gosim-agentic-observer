<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { useRegistrationOpen } from '../../composables/useRegistrationOpen'

const { t, pick } = useI18n()
const { isLoggedIn } = useAuth()
const { registrationOpen } = useRegistrationOpen()
type Step = { n: string; title: string; desc: string }
type TimelineItem = { label: string; desc: string }
const steps = computed(() => t('home.participate.steps') as Step[])
const timeline = computed(() => t('home.participate.timeline') as TimelineItem[])
</script>

<template>
  <section id="participate" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="paper-sheet reveal p-6 md:p-12 lg:p-16">
        <div class="paper-marker paper-marker-top" aria-hidden="true"></div>
        <div class="paper-marker paper-marker-bottom" aria-hidden="true"></div>

        <div class="relative z-10 grid gap-14 lg:grid-cols-[.7fr_1.3fr] lg:gap-20">
          <div>
            <div class="font-mono text-xs text-[#315efb]" aria-hidden="true">03</div>
            <span class="mt-8 inline-block font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ t('home.participate.kicker') }}</span>
            <h2 class="mt-8 max-w-[16ch] text-[clamp(2.25rem,4.6vw,4.75rem)] font-semibold leading-[1.04] tracking-[-.05em] text-[#09090a]">{{ t('home.participate.title') }}</h2>
            <p class="mt-7 max-w-md text-base leading-relaxed text-[#404040] md:text-lg">{{ t('home.participate.lede') }}</p>
          </div>

          <div class="reveal-stagger">
            <article v-for="(step, index) in steps" :key="step.n" class="row-sweep grid grid-cols-[3.5rem_1fr] gap-4 border-t border-black/35 py-7 pl-3 md:grid-cols-[5rem_1fr] md:py-9" :class="index === steps.length - 1 ? 'border-b' : ''">
              <span class="font-mono text-xs tracking-[.15em] text-[#315efb]">{{ step.n }}</span>
              <div>
                <h3 class="text-xl font-semibold leading-tight tracking-[-.03em] text-[#09090a] md:text-2xl">{{ step.title }}</h3>
                <p class="mt-4 max-w-2xl text-sm leading-relaxed text-[#4a4a4a] md:text-base">{{ step.desc }}</p>
              </div>
            </article>

            <div class="reveal-stagger mt-10 grid grid-cols-3 border-y border-black/30">
              <div v-for="item in timeline" :key="item.label" class="border-r border-black/25 py-5 pr-3 last:border-r-0 md:px-5 md:first:pl-0">
                <div class="text-lg font-semibold text-[#315efb] md:text-xl">{{ item.label }}</div>
                <div class="mt-2 font-mono text-xs uppercase leading-relaxed tracking-[.08em] text-[#5f5f5f]">{{ item.desc }}</div>
              </div>
            </div>

            <div class="mt-10 flex flex-wrap gap-3">
              <router-link v-if="isLoggedIn" to="/compete" class="btn dark w-full md:w-auto md:min-w-60">{{ t('dash.new_submission') }} →</router-link>
              <router-link v-else-if="registrationOpen" to="/register" class="btn dark w-full md:w-auto md:min-w-60">{{ t('home.participate.cta') }} →</router-link>
              <router-link to="/start" class="btn outline-dark w-full md:w-auto">{{ t('nav.start') }} →</router-link>
            </div>
          </div>
        </div>
      </div>

      <div class="reveal mt-14 flex flex-col justify-between gap-4 border-t poster-rule pt-6 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary md:flex-row">
        <span>{{ pick('Open worldwide / solo builders welcome', '全球开放 / 欢迎个人开发者') }}</span>
        <span>{{ pick('Same scenario / same simulator / same score', '同一场景 / 同一模拟器 / 同一评分') }}</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.paper-marker {
  position: absolute;
  z-index: 3;
  width: 6rem;
  height: 1.25rem;
  background: #315efb;
  box-shadow: 0 2px 5px rgba(0,0,0,.26);
}
.paper-marker-top { top: -.45rem; left: 8%; transform: rotate(-3deg); }
.paper-marker-bottom { right: 7%; bottom: -.4rem; transform: rotate(4deg); }
</style>
