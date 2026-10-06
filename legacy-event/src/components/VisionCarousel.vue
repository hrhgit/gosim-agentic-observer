<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Slide = { src: string; alt: string; stamp?: string; caption: string; credit?: string }

/**
 * `sky` frames dark full-bleed photography; `plate` frames light-ground plots on
 * the paper sheet the rest of the site uses for them. Same machinery either way —
 * the autoplay gating, keyboard, swipe and pause rules are worth writing once.
 */
const props = withDefaults(
  defineProps<{ slides: Slide[]; interval?: number; variant?: 'sky' | 'plate' }>(),
  { interval: 6000, variant: 'sky' },
)

const index = ref(0)
const paused = ref(false)
const rootEl = ref<HTMLElement | null>(null)
const inView = ref(false)
const tabVisible = ref(true)
let timer: number | undefined
let observer: IntersectionObserver | undefined

const count = computed(() => props.slides.length)

function go(to: number) {
  index.value = (to + count.value) % count.value
}
function next() { go(index.value + 1) }
function prev() { go(index.value - 1) }

const reducedMotion =
  typeof window !== 'undefined' &&
  window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

// Only advance while the strip is actually on screen and in a foreground tab.
// Running it off screen would mean arriving mid-sequence and never seeing the
// first slide, which is where the section's argument starts.
const running = computed(
  () => inView.value && tabVisible.value && !paused.value && !reducedMotion && count.value > 1,
)

watch(running, on => {
  if (timer) { window.clearInterval(timer); timer = undefined }
  if (on) timer = window.setInterval(next, props.interval)
})

function onTabChange() { tabVisible.value = !document.hidden }

onMounted(() => {
  if (rootEl.value) {
    observer = new IntersectionObserver(
      ([entry]) => { inView.value = entry.isIntersecting },
      { threshold: 0.3 },
    )
    observer.observe(rootEl.value)
  }
  document.addEventListener('visibilitychange', onTabChange)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
  observer?.disconnect()
  document.removeEventListener('visibilitychange', onTabChange)
})

// Touch swipe
let startX = 0
function onTouchStart(e: TouchEvent) { startX = e.touches[0].clientX }
function onTouchEnd(e: TouchEvent) {
  const dx = e.changedTouches[0].clientX - startX
  if (Math.abs(dx) > 40) (dx < 0 ? next : prev)()
}
</script>

<template>
  <div
    ref="rootEl"
    class="vision-carousel"
    role="group"
    aria-roledescription="carousel"
    @mouseenter="paused = true"
    @mouseleave="paused = false"
    @focusin="paused = true"
    @focusout="paused = false"
    @keydown.left.prevent="prev"
    @keydown.right.prevent="next"
  >
    <div
      class="carousel-frame"
      :class="variant === 'plate' ? 'is-plate paper-sheet' : 'is-sky h-[340px] md:h-[540px]'"
      @touchstart.passive="onTouchStart"
      @touchend.passive="onTouchEnd"
    >
      <div
        v-for="(slide, i) in slides"
        :key="slide.src"
        class="carousel-slide"
        :class="{ 'is-active': i === index }"
        :aria-hidden="i === index ? undefined : 'true'"
      >
        <img :src="slide.src" :alt="slide.alt" :loading="i === 0 ? 'eager' : 'lazy'">
      </div>

      <div v-if="variant === 'sky'" class="carousel-veil" aria-hidden="true"></div>
      <span v-if="slides[index].stamp" class="carousel-stamp">{{ slides[index].stamp }}</span>

      <button
        v-if="count > 1"
        type="button"
        class="carousel-arrow left-3"
        :aria-label="'Previous slide'"
        @click="prev"
      >‹</button>
      <button
        v-if="count > 1"
        type="button"
        class="carousel-arrow right-3"
        :aria-label="'Next slide'"
        @click="next"
      >›</button>
    </div>

    <div class="mt-5 flex items-start justify-between gap-6">
      <p class="carousel-caption" aria-live="polite">
        {{ slides[index].caption }}
        <span v-if="slides[index].credit" class="carousel-credit">{{ slides[index].credit }}</span>
      </p>

      <div v-if="count > 1" class="carousel-dots" role="tablist">
        <button
          v-for="(slide, i) in slides"
          :key="slide.src"
          type="button"
          role="tab"
          :aria-selected="i === index"
          :aria-label="slide.stamp || slide.alt"
          class="carousel-dot"
          :class="{ 'is-active': i === index }"
          @click="go(i)"
        ></button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.carousel-frame {
  position: relative;
  overflow: hidden;
  box-shadow: 8px 8px 0 #edb28b;
  transform: rotate(-.2deg);
}
.carousel-frame.is-sky { background: #101d29; }

/* Light-ground plots keep their own margins, so the frame takes their shape
   instead of cropping them to fill it. */
.carousel-frame.is-plate {
  aspect-ratio: 1 / 1;
  padding: .85rem;
}
.carousel-frame.is-plate .carousel-slide { inset: .85rem; }
.carousel-frame.is-plate .carousel-slide img { object-fit: contain; }

/* No grayscale here, unlike .photo-wash: two of these slides are figures whose
   colour carries the information. */
.carousel-slide {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity .6s ease;
}
.carousel-slide.is-active { opacity: 1; }
.carousel-slide img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center 45%;
}

.carousel-veil {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    linear-gradient(90deg, rgba(24,36,47,.46), transparent 48%),
    linear-gradient(0deg, rgba(24,36,47,.72), transparent 42%);
}

.carousel-stamp {
  position: absolute;
  z-index: 4;
  right: 1.25rem;
  bottom: 1.1rem;
  max-width: 60%;
  color: rgba(240,233,221,.78);
  font-family: var(--font-mono);
  font-size: .8rem;
  letter-spacing: .14em;
  text-align: right;
  text-transform: uppercase;
}

.carousel-arrow {
  position: absolute;
  top: 50%;
  z-index: 5;
  display: flex;
  width: 2.25rem;
  height: 2.25rem;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(240,233,221,.35);
  background: rgba(16,29,41,.55);
  color: #f0e9dd;
  font-size: 1.35rem;
  line-height: 1;
  transform: translateY(-50%);
  transition: background .2s, border-color .2s;
}
.carousel-arrow:hover { border-color: #edb28b; background: rgba(16,29,41,.85); }
.carousel-arrow:focus-visible { outline: 2px solid #edb28b; outline-offset: 2px; }

.carousel-caption {
  flex: 1;
  min-width: 0;
  color: rgba(240,233,221,.62);
  font-family: var(--font-mono);
  font-size: .75rem;
  line-height: 1.75;
  letter-spacing: .03em;
}
.carousel-credit { opacity: .62; }
.carousel-credit::before { content: ' · '; }

.carousel-dots { display: flex; flex-shrink: 0; gap: .5rem; padding-top: .3rem; }
.carousel-dot {
  width: 1.75rem;
  height: 2px;
  background: rgba(240,233,221,.28);
  transition: background .2s;
}
.carousel-dot.is-active { background: #edb28b; }
.carousel-dot:focus-visible { outline: 2px solid #edb28b; outline-offset: 3px; }

@media (max-width: 768px) {
  .carousel-frame { box-shadow: 6px 6px 0 #edb28b; }
  .carousel-stamp { font-size: .7rem; }
}
</style>
