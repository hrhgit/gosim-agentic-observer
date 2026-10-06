<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import WechatGroup from '../WechatGroup.vue'
import { replayMeta, replaySlots, useReplayClock, SLOT_SECONDS } from '../../composables/useReplayClock'
const { t, tf, pick, locale } = useI18n()

// Click the 900 S label for one true little fact about what fits into 900 seconds.
const FACTS_ZH = [
  '900 秒里，月亮在天上悄悄挪了约 0.14 度——差不多它自己直径的四分之一。',
  '900 秒里，地球转过 3.75 度：头顶的星空滑过了七个多月亮的宽度。',
  '光走 900 秒约 2.7 亿公里——从太阳出发，飞过火星还有富余。',
  '900 秒里，国际空间站飞了近 7000 公里，比北京到莫斯科还远。',
  '900 秒里，旅行者 1 号又离太阳远了约 15000 公里。',
  '真实的巡天望远镜，一晚上要做上百个这样的 900 秒决定。',
]
const FACTS_EN = [
  'In 900 seconds the Moon quietly slides about 0.14° across the sky — a quarter of its own width.',
  'In 900 seconds the Earth turns 3.75°: the sky overhead drifts by seven Moon-widths.',
  'Light travels about 270 million km in 900 seconds — from the Sun past Mars with room to spare.',
  'In 900 seconds the International Space Station covers almost 7,000 km — farther than Beijing to Moscow.',
  'In 900 seconds Voyager 1 gets another 15,000 km farther from the Sun.',
  'A real survey telescope makes a hundred or so of these 900-second calls every single night.',
]
const fact = ref('')
let factTimer: number | undefined
function nextFact() {
  const list = pick(FACTS_EN, FACTS_ZH) as unknown as string[]
  let f = fact.value
  while (f === fact.value) f = list[Math.floor(Math.random() * list.length)]!
  fact.value = f
  if (factTimer) window.clearTimeout(factTimer)
  factTimer = window.setTimeout(() => { fact.value = '' }, 9000)
}
const { state } = useReplayClock()
// A run can in principle arrive without weather rows; the ticker falls back to dashes rather than throwing.
const NO_SLOT = { slot: '—', night: '—', t: '', startSec: 0, open: true, seeing: 0, transp: 0, sky: 0, eff: 0 }
const slot = computed(() => { void replayMeta.version; return replaySlots[state.slotIndex] ?? replaySlots[0] ?? NO_SLOT })
const progressPct = computed(() => `${(state.progress * 100).toFixed(1)}%`)

// The night's own slots, so the tick row can show "every 15 minutes" and the shared weather pattern across it.
const nightSlots = computed(() => { void replayMeta.version; return replaySlots.filter(s => s.night === slot.value.night) })
const posInNight = computed(() => nightSlots.value.findIndex(s => s.slot === slot.value.slot))

const INTL_LOCALE: Record<string, string> = { zh: 'zh-CN', en: 'en-US', ja: 'ja-JP', fr: 'fr-FR' }
const pad = (n: number) => String(n).padStart(2, '0')
const hhmm = (ms: number) => { const d = new Date(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` }
// Readable date comes from the night id (its observing night), not the UTC timestamp, which can already
// have rolled into the next calendar day by the small hours.
const whenLabel = computed(() => {
  const s = slot.value
  const startMs = Date.parse(s.t)
  const year = Number(s.night.slice(1, 5)), month = Number(s.night.slice(5, 7)), day = Number(s.night.slice(7, 9))
  if (!s.t || Number.isNaN(startMs) || Number.isNaN(year) || Number.isNaN(month) || Number.isNaN(day)) return '—'
  const date = new Intl.DateTimeFormat(INTL_LOCALE[locale.value] ?? 'en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(new Date(Date.UTC(year, month - 1, day)))
  return tf('footer.ticker.when', { date, start: hhmm(startMs), end: hhmm(startMs + SLOT_SECONDS * 1000) })
})
</script>

<template>
  <footer class="cosmos-footer border-t text-white">
    <div class="slot-ticker" data-testid="slot-ticker">
      <div class="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-5 gap-y-1.5 px-5 md:px-10 xl:px-14">
        <button type="button" class="ticker-fact-btn" :title="t('footer.ticker.fact_hint')" @click="nextFact">900 S</button>

        <span class="ticker-dome" :class="slot.open ? 'is-open' : 'is-closed'">
          <i class="dome-icon" aria-hidden="true"><i class="dome-leaf dome-leaf-l"></i><i class="dome-leaf dome-leaf-r"></i><i class="dome-core"></i></i>
          <span class="hidden sm:inline">{{ slot.open ? t('footer.ticker.dome_open') : t('footer.ticker.dome_closed') }}</span>
        </span>

        <span v-if="nightSlots.length" class="ticker-ticks hidden sm:inline-flex" role="img" :aria-label="t('footer.ticker.cadence')" :title="t('footer.ticker.cadence')">
          <i v-for="(s, idx) in nightSlots" :key="s.slot" class="ticker-tick" :class="{ 'is-past': idx < posInNight, 'is-now': idx === posInNight, 'is-closed': !s.open }"></i>
        </span>

        <abbr class="ticker-when" :title="t('footer.ticker.code_hint')">{{ whenLabel }} <span class="ticker-code">{{ slot.slot }}</span></abbr>

        <span class="ticker-budget ml-auto" :title="t('footer.ticker.budget_label')">
          <span class="hidden md:inline">{{ t('footer.ticker.budget_label') }}</span>
          <span class="slot-ticker-bar" :class="{ 'is-static': state.reduced }"><i :style="{ width: progressPct }"></i></span>
        </span>

        <span v-if="fact" class="ticker-fact">{{ fact }}</span>
      </div>
    </div>
    <div class="relative z-10 mx-auto max-w-[1600px] px-5 py-10 md:px-10 xl:px-14">
      <div class="flex flex-col justify-between gap-8 md:flex-row md:items-end">
        <div>
          <div class="text-2xl font-semibold tracking-[-.05em] text-[#f5f5f5]">OPEN <span class="text-[#315efb]">/</span> OBSERVER</div>
          <div class="mt-3 max-w-xl text-xs leading-relaxed text-white/60">{{ t('footer.copyright') }}</div>
          <p class="mt-1 max-w-xl text-[11px] leading-relaxed text-white/35">{{ t('footer.license.prefix') }}<a href="https://creativecommons.org/licenses/by-nc/4.0/" target="_blank" rel="noopener" class="underline transition-colors hover:text-white/60">{{ t('footer.license.linkText') }}</a>{{ t('footer.license.suffix') }}</p>
          <WechatGroup compact class="mt-6 text-white/80" />
        </div>
        <nav class="flex flex-wrap gap-5 font-mono text-xs uppercase tracking-[.12em] text-white/70">
          <router-link to="/rules" class="transition-colors hover:text-[#78a6ff]">{{ t('footer.links.rules') }}</router-link>
          <router-link to="/docs" class="transition-colors hover:text-[#78a6ff]">{{ t('footer.links.docs') }}</router-link>
          <router-link to="/announcements" class="transition-colors hover:text-[#78a6ff]">{{ t('nav.announcements') }}</router-link>
          <a :href="`mailto:${t('footer.contact_email')}`" class="transition-colors hover:text-[#78a6ff]">{{ t('footer.links.contact') }}</a>
          <a href="https://gosim.org" target="_blank" rel="noopener" class="transition-colors hover:text-[#78a6ff]">{{ t('footer.mainSite') }} ↗</a>
        </nav>
      </div>
    </div>
  </footer>
</template>

<style scoped>
.slot-ticker { position: relative;
  border-bottom: 1px solid rgba(255,255,255,.14);
  padding: .55rem 0;
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .66rem; letter-spacing: .12em; text-transform: uppercase; color: rgba(255,255,255,.5);
  font-variant-numeric: tabular-nums;
}
.ticker-fact-btn { font: inherit; letter-spacing: inherit; text-transform: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; transition: color .2s; }
.ticker-fact-btn:hover { color: #9db9ff; }
.ticker-fact { position: absolute; left: 1.25rem; bottom: calc(100% + 8px); max-width: min(540px, 86vw); padding: .6rem .85rem; border: 1px solid rgba(148,163,255,.4); background: rgba(6,10,22,.97); color: #dbe6ff; font-size: .68rem; letter-spacing: .04em; line-height: 1.65; text-transform: none; z-index: 40; }

/* Dome state: two half-circle leaves slide apart to reveal the (observing) core, or meet to cover it. */
.ticker-dome { display: inline-flex; align-items: center; gap: .4rem; transition: color .4s; }
.ticker-dome.is-open { color: #78a6ff; }
.ticker-dome.is-closed { color: #ff6b6b; }
.dome-icon { position: relative; display: inline-block; width: .85rem; height: .85rem; flex: none; }
.dome-leaf { position: absolute; top: 0; bottom: 0; width: 50%; background: currentColor; transition: transform .5s cubic-bezier(.4,0,.2,1); }
.dome-leaf-l { left: 0; border-radius: .85rem 0 0 .85rem; transform-origin: right center; }
.dome-leaf-r { right: 0; border-radius: 0 .85rem .85rem 0; transform-origin: left center; }
.dome-core { position: absolute; inset: 2px; border-radius: 50%; background: #070a16; opacity: 0; transition: opacity .35s .15s; }
.ticker-dome.is-open .dome-leaf-l { transform: translateX(-100%); }
.ticker-dome.is-open .dome-leaf-r { transform: translateX(100%); }
.ticker-dome.is-open .dome-core { opacity: 1; animation: dome-breathe 2.6s ease-in-out infinite; }
@keyframes dome-breathe { 0%, 100% { box-shadow: 0 0 2px rgba(120,166,255,.45) inset; } 50% { box-shadow: 0 0 6px rgba(120,166,255,.9) inset; } }

/* One tick per 15-minute slot in the night, lit as the replay advances through it. */
.ticker-ticks { align-items: center; gap: 1px; height: .62rem; }
.ticker-tick { width: 2px; height: .4rem; border-radius: 1px; background: rgba(255,255,255,.16); transition: background-color .3s, height .3s, opacity .3s; }
.ticker-tick.is-closed { background: rgba(255,107,107,.32); }
.ticker-tick.is-past { opacity: .6; }
.ticker-tick.is-now { height: .62rem; background: #78a6ff; box-shadow: 0 0 5px rgba(120,166,255,.85); animation: tick-now-pulse 1.6s ease-in-out infinite; }
.ticker-tick.is-now.is-closed { background: #ff6b6b; box-shadow: 0 0 5px rgba(255,107,107,.85); }
@keyframes tick-now-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }

.ticker-when { text-decoration-style: dotted; text-decoration-color: rgba(255,255,255,.35); cursor: help; }
.ticker-code { color: rgba(255,255,255,.4); text-transform: none; }

.ticker-budget { display: inline-flex; align-items: center; gap: .5rem; }
.slot-ticker-bar { position: relative; width: 6rem; height: 3px; background: rgba(255,255,255,.12); }
.slot-ticker-bar i { position: absolute; left: 0; top: 0; bottom: 0; background: #315efb; transition: width .25s linear; }
.slot-ticker-bar.is-static i { width: 100% !important; transition: none; }

@media (prefers-reduced-motion: reduce) {
  .dome-leaf, .dome-core, .ticker-tick, .slot-ticker-bar i { transition: none; }
  .ticker-dome.is-open .dome-core, .ticker-tick.is-now { animation: none; }
}
</style>
