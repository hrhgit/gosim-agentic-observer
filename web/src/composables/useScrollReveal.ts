import { onMounted, onUnmounted } from 'vue'

const REVEAL_SELECTOR = '.reveal, .reveal-left, .reveal-right, .reveal-blur, .reveal-scale, .reveal-stagger, .rule-draw'

export function useScrollReveal() {
  let observer: IntersectionObserver
  let rafId = 0
  // Cached once per mount: there are at most a couple of these (the hero wash video), and a fresh
  // document-wide query on every single animation frame forever — on every page, whether or not
  // anything is scrolling — was pure waste.
  let parallaxEls: HTMLElement[] = []

  function updateParallax() {
    rafId = 0
    for (const el of parallaxEls) {
      const rect = el.getBoundingClientRect()
      const offset = (rect.top + rect.height / 2) / window.innerHeight
      el.style.setProperty('--parallax-y', `${(offset - 0.5) * -40}px`)
    }
  }
  function onScroll() {
    if (!rafId && parallaxEls.length) rafId = requestAnimationFrame(updateParallax)
  }

  onMounted(() => {
    observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible')
          }
        })
      },
      { threshold: 0.1, rootMargin: '0px 0px -50px 0px' }
    )

    document.querySelectorAll(REVEAL_SELECTOR).forEach((el) => {
      observer.observe(el)
    })

    // safety net: never leave content hidden (printing, screenshots, observers that never fire)
    window.setTimeout(() => document.querySelectorAll(REVEAL_SELECTOR).forEach((el) => el.classList.add('visible')), 2500)

    // Parallax: driven by scroll/resize instead of a free-running per-frame loop.
    parallaxEls = Array.from(document.querySelectorAll('.parallax-bg'))
    if (parallaxEls.length) {
      updateParallax()
      window.addEventListener('scroll', onScroll, { passive: true })
      window.addEventListener('resize', onScroll, { passive: true })
    }
  })

  onUnmounted(() => {
    observer?.disconnect()
    cancelAnimationFrame(rafId)
    window.removeEventListener('scroll', onScroll)
    window.removeEventListener('resize', onScroll)
  })
}
