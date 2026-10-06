<script setup lang="ts">
import { registrationUrl, platformLinks } from '../../registration'
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'

const { t, pick } = useI18n()
type Step = { n: string; title: string; desc: string }
type TimelineItem = { label: string; desc: string }
const links = computed(() => [
  { href: platformLinks.login, label: pick('Log in', '登录账号') },
  { href: platformLinks.team, label: pick('Manage team', '创建 / 加入队伍') },
  { href: platformLinks.submit, label: pick('Submit agent', '提交智能体') },
  { href: platformLinks.resources, label: pick('Starter kit', '入门工具包') },
  { href: platformLinks.docs, label: pick('Documentation', '开发文档') },
  { href: platformLinks.announcements, label: pick('Announcements', '赛事通知') },
])
const steps = computed(() => t('home.participate.steps') as Step[])
const timeline = computed(() => t('home.participate.timeline') as TimelineItem[])
</script>

<template>
  <section id="participate" class="poster-section poster-canvas py-24 md:py-40">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="paper-sheet reveal p-6 md:p-12 lg:p-16">
        <div class="paper-marker paper-marker-top" aria-hidden="true"></div>
        <div class="paper-marker paper-marker-bottom" aria-hidden="true"></div>

        <div class="relative z-10 grid gap-14 lg:grid-cols-[.7fr_1.3fr] lg:gap-20">
          <div>
            <div class="font-mono text-xs text-[#edb28b]" aria-hidden="true">03</div>
            <span class="mt-8 inline-block font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('home.participate.kicker') }}</span>
            <h2 class="mt-8 max-w-[16ch] text-[clamp(2.25rem,4.6vw,4.75rem)] font-semibold leading-[1.04] tracking-[-.05em] text-[#18242f]">{{ t('home.participate.title') }}</h2>
            <p class="mt-7 max-w-md text-base leading-relaxed text-[#41515c] md:text-lg">{{ t('home.participate.lede') }}</p>
          </div>

          <div>
            <article v-for="(step, index) in steps" :key="step.n" class="grid grid-cols-[3.5rem_1fr] gap-4 border-t border-black/35 py-7 md:grid-cols-[5rem_1fr] md:py-9" :class="index === steps.length - 1 ? 'border-b' : ''">
              <span class="font-mono text-xs tracking-[.15em] text-[#edb28b]">{{ step.n }}</span>
              <div>
                <h3 class="text-xl font-semibold leading-tight tracking-[-.03em] text-[#18242f] md:text-2xl">{{ step.title }}</h3>
                <p class="mt-4 max-w-2xl text-sm leading-relaxed text-[#4c5c68] md:text-base">{{ step.desc }}</p>
              </div>
            </article>

            <div class="mt-10 grid grid-cols-3 border-y border-black/30">
              <div v-for="item in timeline" :key="item.label" class="border-r border-black/25 py-5 pr-3 last:border-r-0 md:px-5 md:first:pl-0">
                <div class="text-lg font-semibold text-[#edb28b] md:text-xl">{{ item.label }}</div>
                <div class="mt-2 font-mono text-xs uppercase leading-relaxed tracking-[.08em] text-[#6b7a83]">{{ item.desc }}</div>
              </div>
            </div>

            <a :href="registrationUrl" target="_blank" rel="noopener noreferrer" class="mt-10 inline-flex w-full items-center bg-[#18242f] px-5 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-[#f0e9dd] md:w-auto md:min-w-60">
              {{ t('home.participate.cta') }}
            </a>
            <p class="mt-4 text-sm leading-relaxed text-[#4a4a4a]">{{ pick('Register, form a team and submit on the Agent Observer platform. Each participant needs a platform account.', '报名、组队与提交统一在 Agent Observer 比赛平台完成，每位参赛者需注册平台账号。') }}</p>
            <div class="mt-6 grid grid-cols-2 gap-3 md:grid-cols-3">
              <a v-for="link in links" :key="link.href" :href="link.href" target="_blank" rel="noopener noreferrer" class="border border-black/25 px-4 py-3 text-sm text-[#18242f] transition-colors hover:bg-black/5">{{ link.label }} ↗</a>
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
  background: #edb28b;
  box-shadow: 0 2px 5px rgba(16,29,41,.26);
}
.paper-marker-top { top: -.45rem; left: 8%; transform: rotate(-3deg); }
.paper-marker-bottom { right: 7%; bottom: -.4rem; transform: rotate(4deg); }
</style>
