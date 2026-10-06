<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { assetUrl } from '../../composables/api'
import { usePhaseClock } from '../../composables/usePhaseClock'
import { fmtUtc } from '../../lib/format'
import { meteorShower } from '../../lib/eggs'
import SkyConsole from './SkyConsole.vue'
import HeroGalaxy from './HeroGalaxy.vue'
import HeroCredits from './HeroCredits.vue'
import { competition } from '../../stores/competition'

const { t, tf, pick, locale } = useI18n()
const { current, next, nextStart, nextStartsAt, usingFallback, countdown, loaded } = usePhaseClock()
const heroTitleLines = computed(() => t('hero.titleLines') as string[])
const pad = (n: number) => String(n).padStart(2, '0')
const nextName = computed(() => next.value ? pick(next.value.name_en, next.value.name_zh) : usingFallback.value ? t('phase_clock.fallback_next') : current.value?.ends_at ? tf('phase_clock.ends', { name: pick(current.value.name_en, current.value.name_zh) }) : t('phase_clock.none_scheduled'))
const parts = computed(() => [
  { v: String(countdown.value.days), l: t('phase_clock.days') },
  { v: pad(countdown.value.hours), l: t('phase_clock.hours') },
  { v: pad(countdown.value.minutes), l: t('phase_clock.minutes') },
  { v: pad(countdown.value.seconds), l: t('phase_clock.seconds') },
])
// Milestones of the event, shown as a horizontal timeline under the phase strip.
// The next upcoming stage carries a D-day chip; a stage whose window contains today reads LIVE.
const STAGE_WINDOWS: [string, string][] = [['2026-10-01', '2026-10-04'], ['2026-10-05', '2026-10-07'], ['2026-10-17', '2026-10-17']]
function stageChip(i: number): string {
  if (competition.mode==='practice') return ''
  const [from, to] = STAGE_WINDOWS[i] ?? ['', '']
  if (!from) return ''
  const day = 86_400_000
  const now = Date.now()
  if (now >= Date.parse(from) && now < Date.parse(to) + day) return 'LIVE'
  const ahead = Math.ceil((Date.parse(from) - now) / day)
  if (ahead <= 0) return ''
  const firstUpcoming = STAGE_WINDOWS.findIndex(([, end]) => now < Date.parse(end) + day)
  return i === firstUpcoming ? `D-${ahead}` : ''
}
// Seven quick taps on the backdrop (not on links or the console) pour a meteor shower.
let taps: number[] = []
function onHeroTap(e: MouseEvent) {
  const el = e.target as HTMLElement
  if (!el.closest('.hero-section') || el.closest('a, button, input, select, textarea, .sky-console')) return
  const now = Date.now()
  taps = taps.filter(ts => now - ts < 6000)
  taps.push(now)
  if (taps.length >= 7) { taps = []; meteorShower(pick('☄️ Comet catalogued', '☄️ 彗星已记入目录')) }
}
onMounted(() => document.addEventListener('click', onHeroTap))
onBeforeUnmount(() => document.removeEventListener('click', onHeroTap))

type Stage = { label: string; date: string; note?: string }
const stages = computed(() => t('hero.pipeline') as Stage[])
</script>

<template>
  <section id="top" class="hero-section cosmos-hero poster-canvas">
    <div class="hero-wash" aria-hidden="true">
      <video
        class="hero-wash-video parallax-bg"
        autoplay muted loop playsinline
        preload="auto"
        :poster="assetUrl('/media/survey-milky-way.webp')"
      >
        <source :src="assetUrl('/media/survey-night-sky.mp4')" type="video/mp4">
      </video>
    </div>
    <div class="hero-grid-lines" aria-hidden="true"></div>
    <div class="hero-beam" aria-hidden="true"></div>
    <HeroGalaxy />

    <div class="relative z-10 mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="hero-grid">
        <div class="hero-copy">
          <div class="hero-kicker mb-6 flex items-center gap-4 font-mono text-xs uppercase leading-relaxed tracking-[.12em] text-[#78a6ff] md:text-sm reveal">
            <span class="live-dot h-2 w-2 bg-[#78a6ff]"></span>
            {{ t('hero.eyebrow') }}
          </div>

          <h1 class="hero-title reveal reveal-delay-1" :class="{ 'hero-title-zh': locale === 'zh' }" :aria-label="t('hero.system')">
            <span v-for="line in heroTitleLines" :key="line" class="hero-title-line">{{ line }}</span>
          </h1>
          <p class="hero-subtitle mt-3 font-mono text-sm uppercase tracking-[.22em] text-[#78a6ff] reveal reveal-delay-2">{{ t('hero.subtitle') }}</p>

          <p class="mt-7 max-w-xl text-base leading-[1.75] text-white/92 md:text-lg reveal reveal-delay-3">{{ t('hero.lede') }}</p>
          <div class="mt-7 flex flex-wrap gap-3 reveal reveal-delay-4">
            <router-link to="/start" class="hero-action hero-action-primary" data-testid="hero-cta-start">
              {{ t('hero.cta_start') }} <span>→</span>
            </router-link>
            <router-link to="/leaderboard" class="hero-action" data-testid="hero-cta-board">
              {{ t('nav.leaderboard') }} <span>→</span>
            </router-link>
          </div>
          <HeroCredits class="mt-6 reveal reveal-delay-4" />

          <div class="phase-strip phase-strip-live mt-9 reveal reveal-delay-5" data-testid="phase-strip">
            <div class="phase-strip-next" data-testid="phase-next">
              <span class="phase-strip-label">{{ t('phase_clock.next') }}</span>
              <p v-if="nextStart && next?.starts_at" class="phase-strip-stage">
                <b>{{ nextStart.name }}</b> · <time :datetime="next.starts_at" :title="nextStart.moment">{{ nextStart.day }}</time> · <span>{{ nextStart.left }}</span>
              </p>
              <p v-else class="phase-strip-stage is-quiet">{{ loaded ? nextName : '…' }}<template v-if="nextStartsAt"> · {{ fmtUtc(nextStartsAt) }} UTC</template></p>
              <div v-if="nextStartsAt" class="phase-countdown" role="timer" :aria-label="t('phase_clock.countdown_aria')">
                <span v-for="p in parts" :key="p.l"><b class="phase-countdown-value">{{ p.v }}</b><small>{{ p.l }}</small></span>
              </div>
            </div>
          </div>
          <ol class="hero-timeline mt-9 reveal reveal-delay-5" data-testid="hero-timeline">
            <li v-for="(stage, i) in stages" :key="stage.label">
              <span class="hero-timeline-step">0{{ i + 1 }}</span>
              <span class="hero-timeline-label">{{ stage.label }}</span>
              <span class="hero-timeline-date">{{ stage.date }}<i v-if="stageChip(i)" class="hero-dday" :class="{ live: stageChip(i) === 'LIVE' }">{{ stageChip(i) }}</i></span>
              <span v-if="stage.note" class="hero-timeline-note">{{ stage.note }}</span>
            </li>
          </ol>
        </div>

        <div class="hero-console reveal reveal-delay-2">
          <SkyConsole />
        </div>
      </div>

    </div>

    <div class="hero-side-note" aria-hidden="true">{{ t('hero.side_note') }}</div>
  </section>
</template>

<style scoped>
.cosmos-hero {
  color: #f7f9ff;
  background:
    radial-gradient(circle at 18% 20%, rgba(49,94,251,.32), transparent 26%),
    radial-gradient(circle at 82% 12%, rgba(139,92,246,.2), transparent 24%),
    radial-gradient(circle at 62% 88%, rgba(34,211,238,.14), transparent 30%),
    linear-gradient(180deg, #02050c 0%, #060a16 58%, #030612 100%);
}
.hero-wash {
  position: absolute; z-index: 0; inset: 0; pointer-events: none; overflow: hidden;
}
.hero-wash::after {
  position: absolute; inset: 0; content: '';
  background: linear-gradient(90deg, rgba(2,5,12,.7) 0%, rgba(2,5,12,.34) 46%, rgba(2,5,12,.72) 100%),
    linear-gradient(0deg, rgba(2,5,12,.92), transparent 38%, transparent 76%, rgba(2,5,12,.7));
}
.hero-wash-video {
  /* A square box pinned to the bottom-right corner, so the radial mask below lines up with the
     footage itself and the frame dissolves into the page instead of ending on a hard edge. */
  position: absolute; right: 0; bottom: 0;
  height: 100%; width: auto; aspect-ratio: 1 / 1; object-fit: cover;
  opacity: .52;
  filter: saturate(1.2) contrast(1.22) brightness(1.12);
  transform: translate3d(0, var(--parallax-y, 0px), 0) scale(.5); transform-origin: right bottom;
  transition: transform .18s linear;
  -webkit-mask-image: radial-gradient(circle at 50% 50%, #000 34%, rgba(0,0,0,.72) 58%, rgba(0,0,0,.22) 78%, rgba(0,0,0,0) 92%);
  mask-image: radial-gradient(circle at 50% 50%, #000 34%, rgba(0,0,0,.72) 58%, rgba(0,0,0,.22) 78%, rgba(0,0,0,0) 92%);
}
.hero-beam {
  position: absolute; z-index: 1; top: 0; bottom: 0; left: 34%; width: 34rem; pointer-events: none;
  background: linear-gradient(100deg, transparent, rgba(120,166,255,.12), transparent);
  transform: translateX(-60%);
  animation: hero-beam-sweep 9s ease-in-out infinite;
}
@keyframes hero-beam-sweep {
  0%, 100% { transform: translateX(-70%); opacity: 0; }
  35% { opacity: 1; }
  55% { transform: translateX(130%); opacity: 0; }
}
.hero-grid {
  display: grid; gap: 3rem; align-items: center;
  padding: clamp(2.5rem, 6vw, 5rem) 0 clamp(2.5rem, 5vw, 4rem);
  min-height: calc(100svh - 12rem);
}
@media (min-width: 1024px) {
  .hero-grid { grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: clamp(2.5rem, 5vw, 6rem); }
}
.hero-copy { min-width: 0; }
.hero-console { min-width: 0; }

.hero-title {
  max-width: 11ch; color: #f7f9ff;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  font-size: clamp(3.4rem, 6vw, 6.4rem); font-weight: 600; letter-spacing: -.065em; line-height: .92; text-wrap: balance;
  background: linear-gradient(100deg, #ffffff 0%, #f4f7ff 48%, #dfe8ff 76%, #c7e4ff 100%);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.hero-title-line { display: block; }
.hero-title-zh { max-width: none; font-size: clamp(3.2rem, 5.6vw, 5.8rem); line-height: 1.04; }

.hero-action {
  position: relative;
  overflow: hidden;
  display: inline-flex; min-width: 11.5rem; min-height: 48px; align-items: center; justify-content: space-between;
  border: 1px solid rgba(217,229,255,.48); padding: .8rem 1rem; color: #f7f9ff; background: rgba(2,8,20,.46);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .75rem; letter-spacing: .11em; text-transform: uppercase;
  transition: color .2s ease, background .2s ease, border-color .2s ease;
}
.hero-action:hover { color: #06102a; border-color: #f7f9ff; background: #f7f9ff; }
.hero-action-primary { color: #ffffff; border-color: #315efb; background: linear-gradient(92deg, #315efb, #7c5cff); }

.hero-grid-lines {
  position: absolute; z-index: 0; inset: 0; pointer-events: none; opacity: .16;
  background-image:
    linear-gradient(rgba(120,166,255,.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(120,166,255,.08) 1px, transparent 1px);
  background-size: 72px 72px;
  mask-image: linear-gradient(180deg, transparent, #000 12%, #000 84%, transparent);
}

.phase-strip {
  display: grid; grid-template-columns: 1fr; gap: 0; border-top: 1px solid rgba(255,255,255,.25); border-bottom: 1px solid rgba(255,255,255,.25);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
}
.phase-strip > div { display: flex; flex-direction: column; justify-content: center; padding: .9rem 0; min-width: 0; }
.phase-strip-label { font-size: .64rem; letter-spacing: .14em; text-transform: uppercase; color: rgba(255,255,255,.5); }
.phase-strip-stage {
  margin: .35rem 0 0; font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  font-size: .98rem; line-height: 1.5; color: rgba(226,234,255,.86);
}
.phase-strip-stage b { font-weight: 650; color: #fff; }
.phase-strip-stage time { color: #cfe0ff; }
.phase-strip-stage.is-quiet { font-size: .82rem; color: rgba(255,255,255,.62); }
.phase-countdown { display: flex; gap: 1.25rem; margin-top: .45rem; font-variant-numeric: tabular-nums; }
.phase-countdown span { display: flex; align-items: baseline; gap: .35rem; }
.phase-countdown-value {
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  font-size: clamp(1.9rem, 3.4vw, 2.9rem); font-weight: 600; letter-spacing: -.04em; color: #f7f9ff;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 26px rgba(120,166,255,.42);
}
.phase-countdown small { font-size: .62rem; letter-spacing: .1em; text-transform: uppercase; color: rgba(255,255,255,.45); }

.hero-metrics { position: relative; }
.hero-metrics > div { position: relative; padding-left: clamp(.65rem, 2vw, 1.5rem); padding-right: clamp(.65rem, 2vw, 1.5rem); }
@media (min-width: 768px) { .hero-metrics { grid-template-columns: repeat(var(--metric-cols, 3), minmax(0, 1fr)); } }
.hero-metrics > div:first-child { padding-left: 0; }

/* Every rule in this strip fades out at its ends rather than stopping on a hard edge. */
.hero-metric::after {
  position: absolute; top: .25rem; bottom: .25rem; right: 0; width: 1px; content: '';
  background: linear-gradient(180deg, rgba(255,255,255,0), rgba(255,255,255,.32) 36%, rgba(255,255,255,.15) 76%, rgba(255,255,255,0));
  opacity: 0;
}
@media (min-width: 768px) { .hero-metric.metric-rule-md::after { opacity: 1; } }
@media (max-width: 767px) {
  .hero-metric.metric-rule-sm::after { opacity: 1; }
  .hero-metrics > div:nth-child(odd) { padding-left: 0; }
  .hero-metrics > div:nth-child(-n+2)::before {
    position: absolute; left: 0; right: 0; bottom: 0; height: 1px; content: '';
    background: linear-gradient(90deg, rgba(255,255,255,.26), rgba(255,255,255,.04));
  }
}

.hero-timeline-note { display: block; margin-top: .3rem; font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .62rem; letter-spacing: .05em; color: rgba(214,226,255,.8); }
.hero-timeline {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0; padding: 0; list-style: none;
  border-top: 1px solid rgba(255,255,255,.25);
}
.hero-timeline li { position: relative; display: flex; flex-direction: column; gap: .45rem; padding: 1.7rem 1.4rem 0 0; }
.hero-timeline li::before {
  position: absolute; top: -5px; left: 0; width: 9px; height: 9px; content: '';
  background: #78a6ff; transform: rotate(45deg);
  box-shadow: 0 0 14px rgba(120,166,255,.6);
}
.hero-timeline li::after {
  position: absolute; top: -1px; left: 9px; right: 0; height: 1px; content: '';
  background: linear-gradient(90deg, rgba(120,166,255,.55), rgba(255,255,255,.14));
}
.hero-timeline-step { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .74rem; letter-spacing: .16em; color: #8fb4ff; }
.hero-dday { margin-left: .5rem; padding: .12rem .45rem; border: 1px solid rgba(245,185,66,.55); font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .62rem; font-style: normal; letter-spacing: .12em; color: #ffd27a; animation: dday-breathe 2.6s ease-in-out infinite; }
.hero-dday.live { border-color: rgba(94,234,161,.6); color: #7ef0b0; }
@keyframes dday-breathe { 0%, 100% { box-shadow: 0 0 0 rgba(245,185,66,0); } 50% { box-shadow: 0 0 12px rgba(245,185,66,.35); } }
.hero-timeline-label { font-size: 1.18rem; font-weight: 650; letter-spacing: -.015em; color: #ffffff; }
.hero-timeline-date { font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .76rem; letter-spacing: .1em; text-transform: uppercase; color: rgba(255,255,255,.68); }
.hero-timeline-note { display: block; margin-top: .15rem; max-width: 16rem; font-size: .8rem; line-height: 1.55; color: rgba(226,234,255,.92); }

.hero-metric-value { color: #f7f9ff; }
.hero-metrics-strong { background: linear-gradient(180deg, rgba(49,94,251,.1), transparent 70%); }
.hero-metrics-strong::before {
  position: absolute; top: 0; left: 0; right: 0; height: 1px; content: '';
  background: linear-gradient(90deg, rgba(255,255,255,0), rgba(255,255,255,.44) 10%, rgba(255,255,255,.44) 86%, rgba(255,255,255,0));
}

.hero-side-note {
  position: absolute; z-index: 3; top: 50%; right: -8.4rem;
  color: rgba(255,255,255,.4); font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .75rem; letter-spacing: .18em; text-transform: uppercase; transform: rotate(90deg);
}
@media (max-width: 1699px) { .hero-side-note { display: none; } }
@media (max-width: 1023px) {
  .hero-wash-video { opacity: .32; }
  .hero-grid { min-height: 0; }
.hero-timeline { grid-template-columns: 1fr; }
  .hero-timeline li::after { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .hero-wash-video { transform: none; transition: none; }
  .hero-beam { display: none; }
  .hero-action::after { display: none; }
}

@media (max-width: 720px) {
  .hero-title { font-size: clamp(3rem, 15vw, 4.5rem); }
  .hero-title-zh { max-width: none; font-size: clamp(2.8rem, 14vw, 4rem); }
  .hero-action { min-width: calc(50% - .4rem); }
  .phase-countdown { gap: .9rem; }
}
</style>
