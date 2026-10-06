<script setup lang="ts">
import { ref } from 'vue'
import { assetUrl } from '../../composables/api'
import { useI18n } from '../../composables/useI18n'

const { t } = useI18n()
const player = ref<HTMLVideoElement | null>(null)
const started = ref(false)

// The film carries a voice track, so it never starts on its own. One click starts it with sound;
// from then on the browser's own controls are in charge.
function start() {
  started.value = true
  player.value?.play().catch(() => { /* blocked: the controls are already there to press */ })
}
</script>

<template>
  <section id="film" class="poster-section poster-canvas py-16 md:py-24" data-testid="film-section">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="mx-auto max-w-[1100px]">
        <span class="poster-kicker reveal">{{ t('home.film.kicker') }}</span>

        <div class="film-frame reveal reveal-delay-1 mt-8">
          <video
            ref="player"
            class="film-video"
            controls
            playsinline
            preload="none"
            :poster="assetUrl('/media/survey-film-poster.webp')"
          >
            <source :src="assetUrl('/media/survey-film.mp4')" type="video/mp4">
          </video>
          <button v-if="!started" type="button" class="film-play" :aria-label="t('home.film.play')" @click="start">
            <span class="film-play-mark" aria-hidden="true">▶</span>
            <span class="film-play-text">{{ t('home.film.play') }}</span>
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.film-frame {
  position: relative;
  border: 1px solid rgba(255,255,255,.28);
  border-image: linear-gradient(180deg, rgba(255,255,255,.4), rgba(255,255,255,.28) 45%, rgba(255,255,255,.1)) 1;
  background: #05070e;
}
.film-frame::before {
  position: absolute; top: -1px; left: 0; width: 3.5rem; height: 2px; content: ''; background: #315efb;
}
.film-video { display: block; width: 100%; height: auto; aspect-ratio: 16 / 9; background: #05070e; }

/* Sits over the poster frame until the first press, then hands over to the real controls. */
.film-play {
  position: absolute; inset: 0;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: .9rem;
  border: 0; cursor: pointer; color: #f7f9ff;
  background: linear-gradient(180deg, rgba(4,7,16,.12), rgba(4,7,16,.62));
  transition: background .2s ease;
}
.film-play:hover { background: linear-gradient(180deg, rgba(4,7,16,.04), rgba(4,7,16,.5)); }
.film-play-mark {
  display: flex; align-items: center; justify-content: center;
  width: 4.5rem; height: 4.5rem; padding-left: .25rem;
  border: 1px solid rgba(255,255,255,.55);
  font-size: 1.35rem;
  background: rgba(8,12,24,.5);
  -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px);
  transition: border-color .2s ease, background .2s ease, transform .2s ease;
}
.film-play:hover .film-play-mark { border-color: #78a6ff; background: rgba(49,94,251,.35); transform: scale(1.04); }
.film-play-text {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .72rem; letter-spacing: .16em; text-transform: uppercase;
  color: rgba(247,249,255,.88);
}
.film-play:focus-visible { outline: 2px solid #78a6ff; outline-offset: -4px; }
@media (prefers-reduced-motion: reduce) {
  .film-play, .film-play-mark { transition: none; }
  .film-play:hover .film-play-mark { transform: none; }
}
</style>
