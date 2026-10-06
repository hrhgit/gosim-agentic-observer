<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

type Slide = { src: string; avif?: string; webp?: string; alt: string; stamp?: string; caption: string; credit?: string }

// Ported from the landing site: the sky -> human observer -> agent narrative strip.
const props = withDefaults(
  defineProps<{ slides: Slide[]; interval?: number }>(),
  { interval: 6000 },
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

// Only advance while the strip is on screen in a foreground tab, so viewers
// always arrive at the first slide, where the section's argument starts.
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
      ([entry]) => { inView.value = entry!.isIntersecting },
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
function onTouchStart(e: TouchEvent) { startX = e.touches[0]!.clientX }
function onTouchEnd(e: TouchEvent) {
  const dx = e.changedTouches[0]!.clientX - startX
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
      class="carousel-frame h-[340px] md:h-[540px]"
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
        <picture>
          <source v-if="slide.avif" :srcset="slide.avif" type="image/avif">
          <source v-if="slide.webp" :srcset="slide.webp" type="image/webp">
          <img :src="slide.src" :alt="slide.alt" :loading="i === 0 ? 'eager' : 'lazy'">
        </picture>
      </div>

      <div class="carousel-veil" aria-hidden="true"></div>
      <span v-if="slides[index]!.stamp" class="carousel-stamp">{{ slides[index]!.stamp }}</span>

      <button
        v-if="count > 1"
        type="button"
        class="carousel-arrow left-3"
        aria-label="Previous slide"
        @click="prev"
      >‹</button>
      <button
        v-if="count > 1"
        type="button"
        class="carousel-arrow right-3"
        aria-label="Next slide"
        @click="next"
      >›</button>
    </div>

    <div class="mt-5 flex items-start justify-between gap-6">
      <p class="carousel-caption" aria-live="polite">
        {{ slides[index]!.caption }}
        <span v-if="slides[index]!.credit" class="carousel-credit">{{ slides[index]!.credit }}</span>
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
  background: #0b1222;
  border: 1px solid rgba(158,173,255,.25);
  box-shadow: 8px 8px 0 rgba(49,94,251,.85);
}

.carousel-slide {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity .6s ease;
}
.carousel-slide.is-active { opacity: 1; }
.carousel-slide picture { display: block; width: 100%; height: 100%; }
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
    linear-gradient(90deg, rgba(5,9,20,.5), transparent 48%),
    linear-gradient(0deg, rgba(5,9,20,.74), transparent 42%);
}

.carousel-stamp {
  position: absolute;
  z-index: 4;
  right: 1.25rem;
  bottom: 1.1rem;
  max-width: 60%;
  color: rgba(226,234,255,.82);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
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
  border: 1px solid rgba(226,234,255,.35);
  background: rgba(5,9,20,.55);
  color: #e8eeff;
  font-size: 1.35rem;
  line-height: 1;
  transform: translateY(-50%);
  transition: background .2s, border-color .2s;
}
.carousel-arrow:hover { border-color: #78a6ff; background: rgba(5,9,20,.85); }
.carousel-arrow:focus-visible { outline: 2px solid #78a6ff; outline-offset: 2px; }

.carousel-caption {
  flex: 1;
  min-width: 0;
  color: rgba(226,234,255,.66);
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .75rem;
  line-height: 1.75;
  letter-spacing: .03em;
}
.carousel-credit { opacity: .62; }
.carousel-credit::before { content: ' · '; }

.carousel-dots { display: flex; flex-shrink: 0; gap: .5rem; padding-top: .3rem; }
.carousel-dot {
  position: relative;
  width: 1.75rem;
  height: 2px;
  background: rgba(226,234,255,.28);
  transition: background .2s;
}
/* The bar stays 2px thin; an invisible 24px-tall hit area makes it easy to tap. */
.carousel-dot::before { position: absolute; inset: -11px -.25rem; content: ''; }
.carousel-dot.is-active { background: #78a6ff; }
.carousel-dot:focus-visible { outline: 2px solid #78a6ff; outline-offset: 3px; }

@media (max-width: 768px) {
  .carousel-frame { box-shadow: 6px 6px 0 rgba(49,94,251,.85); }
  .carousel-stamp { font-size: .7rem; }
}
</style>
