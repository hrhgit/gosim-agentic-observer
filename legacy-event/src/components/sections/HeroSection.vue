<script setup lang="ts">
import { registrationUrl } from '../../registration'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { assetUrl } from '../../composables/api'

const { t, pick, locale } = useI18n()
type Step = { label: string; date: string }
const pipeline = computed(() => t('hero.pipeline') as Step[])
const heroVideo = ref<HTMLVideoElement | null>(null)
const slowDown = () => { if (heroVideo.value) heroVideo.value.playbackRate = 0.5 }
onMounted(slowDown)
const heroTitleLines = computed(() => locale.value === 'zh'
  ? ['巡天智能体']
  : ['Agent Observer'])
</script>

<template>
  <section id="top" class="hero-section cosmos-hero poster-canvas">
    <div
      class="hero-media"
      aria-hidden="true"
      :style="{ backgroundImage: `url(${assetUrl('/media/survey-milky-way.jpg')})` }"
    >
      <video ref="heroVideo" autoplay loop muted playsinline preload="metadata" @loadedmetadata="slowDown" @play="slowDown" :poster="assetUrl('/media/survey-milky-way.jpg')">
        <source :src="assetUrl('/media/survey-night-sky.mp4')" type="video/mp4">
      </video>
    </div>
    <div class="hero-overlay" aria-hidden="true"></div>

    <div class="hero-layout relative z-10 mx-auto flex min-h-[calc(100svh-4rem)] max-w-[1600px] flex-col px-5 md:px-10 xl:px-14">
      <div class="hero-stage flex flex-1 items-center py-10">
        <div class="hero-copy">
          <div class="hero-kicker mb-7 flex items-center gap-4 font-mono text-xs uppercase leading-relaxed tracking-[.12em] text-[#edb28b] md:text-sm">
            <span class="live-dot h-2 w-2 bg-[#edb28b]"></span>
            {{ t('hero.eyebrow') }}
          </div>

          <h1 class="hero-title" :class="{ 'hero-title-zh': locale === 'zh' }" :aria-label="t('hero.system')">
            <span v-for="line in heroTitleLines" :key="line" class="hero-title-line">{{ line }}</span>
          </h1>
          <p class="hero-subtitle mt-4 font-mono text-sm uppercase tracking-[.22em] text-[#edb28b] md:text-base">{{ t('hero.subtitle') }}</p>

          <div class="hero-intro mt-8 max-w-3xl border-t border-white/25 pt-6">
            <p class="text-base leading-[1.75] text-white/82 md:text-lg">{{ t('hero.lede') }}</p>
            <div class="mt-7 flex flex-wrap gap-3">
              <a :href="registrationUrl" target="_blank" rel="noopener noreferrer" class="hero-action hero-action-primary ">
                {{ pick('Register now', '立即报名') }}
              </a>
              <router-link to="/brief" class="hero-action">
                {{ pick('Mission brief', '赛题简报') }} <span>→</span>
              </router-link>
            </div>
          </div>

          <div class="hero-sponsors mt-7" :aria-label="pick('Sponsors', '赞助商')">
            <span class="hero-sponsors-label">{{ pick('Sponsors', '赞助商') }}</span>
            <div class="hero-sponsors-logos">
              <a href="https://www.minimaxi.com/" target="_blank" rel="noopener noreferrer" aria-label="MiniMax">
                <img :src="assetUrl('/sponsors/minimax.png')" alt="MiniMax" class="hero-sponsor-logo hero-sponsor-logo--minimax" />
              </a>
              <a href="https://www.kimi.com/" target="_blank" rel="noopener noreferrer" aria-label="Kimi">
                <img :src="assetUrl('/sponsors/kimi.png')" alt="Kimi" class="hero-sponsor-logo hero-sponsor-logo--kimi" />
              </a>
            </div>
          </div>
        </div>

      </div>

      <div class="hero-timeline grid grid-cols-3 border-t border-white/22">
        <div v-for="(step, index) in pipeline" :key="step.label" class="border-r border-white/16 py-5 last:border-r-0 md:py-6">
          <span class="font-mono text-xs text-[#edb28b]">0{{ index + 1 }}</span>
          <p class="mt-2 text-sm font-semibold leading-snug">{{ step.label }}</p>
          <p class="mt-1 font-mono text-xs leading-snug tracking-[.04em] text-white/50">{{ step.date }}</p>
        </div>
      </div>
    </div>

    <div class="hero-side-note" aria-hidden="true">Human judgment / machine speed / one shared sky</div>
  </section>
</template>

<style scoped>
.cosmos-hero {
  min-height: 760px;
  color: #f0e9dd;
  background: #0d1822;
}

.hero-media {
  position: absolute;
  z-index: 0;
  inset: 0;
  background-color: #0d1822;
  background-position: center bottom;
  background-size: cover;
}

.hero-media video {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center bottom;
  filter: saturate(1.16) contrast(1.06) brightness(.94);
}

.hero-media::after {
  position: absolute;
  inset: 0;
  content: '';
  background:
    linear-gradient(90deg, rgba(13,24,34,.9) 0%, rgba(16,29,41,.62) 42%, rgba(13,24,34,.12) 76%, rgba(13,24,34,.24) 100%),
    linear-gradient(0deg, rgba(13,24,34,.72) 0%, rgba(13,24,34,.1) 54%, rgba(13,24,34,.3) 100%);
}

.hero-overlay {
  position: absolute;
  z-index: 1;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 62% 34%, rgba(237,178,139,.1), transparent 27%),
    linear-gradient(180deg, transparent 72%, rgba(13,24,34,.4));
}

.hero-layout { min-height: max(760px, calc(100svh - 4rem)); }
.hero-copy { max-width: 850px; }

.hero-title {
  max-width: 11ch;
  color: #f0e9dd;
  font-family: var(--font-serif);
  font-size: clamp(4.5rem, 8.4vw, 9rem);
  font-weight: 500;
  letter-spacing: -.02em;
  line-height: .95;
  text-wrap: balance;
}

.hero-title-line { display: block; }
.hero-title-zh {
  font-size: clamp(3.4rem, 6.4vw, 6.75rem);
  font-weight: 600;
  line-height: 1.1;
  letter-spacing: .02em;
}
.hero-title-zh + .hero-subtitle { margin-top: 1.5rem; }

.hero-action {
  display: inline-flex;
  min-width: 11.5rem;
  min-height: 48px;
  align-items: center;
  justify-content: space-between;
  border: 1px solid rgba(240,233,221,.48);
  padding: .8rem 1rem;
  color: #f0e9dd;
  background: rgba(16,29,41,.46);
  font-family: var(--font-mono);
  font-size: .75rem;
  letter-spacing: .11em;
  text-transform: uppercase;
  transition: color .2s ease, background .2s ease, border-color .2s ease;
}

.hero-action:hover { color: #202b36; border-color: #f0e9dd; background: #f0e9dd; }
.hero-action-primary { color: #18242f; border-color: #edb28b; background: #edb28b; }
.hero-timeline > div { padding-left: clamp(.65rem, 2vw, 1.5rem); }

.hero-sponsors { display: flex; flex-wrap: wrap; align-items: center; gap: .9rem 1.25rem; }
.hero-sponsors-label { color: rgba(240,233,221,.6); font-family: var(--font-mono); font-size: .7rem; letter-spacing: .13em; text-transform: uppercase; }
.hero-sponsors-logos { display: flex; align-items: center; gap: 1.25rem; }
.hero-sponsors-logos a { display: flex; align-items: center; opacity: .82; transition: opacity .2s ease, transform .2s ease; }
.hero-sponsors-logos a:hover, .hero-sponsors-logos a:focus-visible { opacity: 1; outline: none; transform: translateY(-1px); }
.hero-sponsor-logo { width: auto; object-fit: contain; }
.hero-sponsor-logo--minimax { height: 2.25rem; filter: brightness(0) invert(1); }
.hero-sponsor-logo--kimi { height: 2.4rem; }

.hero-side-note {
  position: absolute;
  z-index: 3;
  top: 50%;
  right: -8.4rem;
  color: rgba(240,233,221,.4);
  font-family: var(--font-mono);
  font-size: .75rem;
  letter-spacing: .18em;
  text-transform: uppercase;
  transform: rotate(90deg);
}

@media (max-width: 1023px) {
  .hero-stage { align-items: end; }
  .hero-title { max-width: 12ch; font-size: clamp(3.8rem, 12vw, 7rem); }
  .hero-side-note { display: none; }
}

@media (max-width: 720px) {
  .cosmos-hero { min-height: 900px; }
  .hero-layout { min-height: 900px; }
  .hero-media video { object-position: center bottom; }
  .hero-media::after {
    background:
      linear-gradient(90deg, rgba(13,24,34,.82), rgba(13,24,34,.28)),
      linear-gradient(0deg, rgba(13,24,34,.78), transparent 58%, rgba(13,24,34,.34));
  }
  .hero-stage { gap: 2rem; padding-top: 2rem; }
  .hero-title { font-size: clamp(3.6rem, 18vw, 5.5rem); }
  .hero-title-zh { font-size: clamp(3.15rem, 16vw, 4.75rem); line-height: 1.04; }
  .hero-intro { margin-top: 1.5rem; padding-top: 1.25rem; }
  .hero-action { min-width: calc(50% - .4rem); }
  .hero-sponsors { margin-top: 1.5rem; }
}
</style>
