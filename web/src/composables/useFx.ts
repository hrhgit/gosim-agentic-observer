/** Site-wide motion toolkit: click sparks, 3D card tilt, count-up numbers.
 *  Everything degrades to nothing under prefers-reduced-motion or on touch devices. */
import type { Directive } from 'vue'

const reduced = () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
const finePointer = () => typeof window !== 'undefined' && window.matchMedia('(pointer: fine)').matches

/** A radial pulse plus a handful of flying sparks at every pointer press — the "impact" layer. */
export function installClickSparks(): void {
  if (reduced()) return
  document.addEventListener('pointerdown', event => {
    if (event.button !== 0) return
    const burst = document.createElement('span')
    burst.className = 'fx-burst'
    burst.style.left = `${event.clientX}px`
    burst.style.top = `${event.clientY}px`
    const onTarget = (event.target as HTMLElement | null)?.closest('a, button, .quest-card, .wall-card, [role="tab"]')
    burst.classList.toggle('fx-burst-strong', Boolean(onTarget))
    for (let i = 0; i < 7; i += 1) {
      const spark = document.createElement('i')
      const angle = (Math.PI * 2 * i) / 7 + Math.random() * 0.6
      const distance = (onTarget ? 34 : 22) + Math.random() * 18
      spark.style.setProperty('--fx-dx', `${Math.cos(angle) * distance}px`)
      spark.style.setProperty('--fx-dy', `${Math.sin(angle) * distance}px`)
      burst.appendChild(spark)
    }
    document.body.appendChild(burst)
    window.setTimeout(() => burst.remove(), 620)
  }, { passive: true })
}

/** v-tilt: perspective tilt + glare that follows the pointer. */
export const vTilt: Directive<HTMLElement> = {
  mounted(el) {
    if (reduced() || !finePointer()) return
    el.classList.add('fx-tilt')
    let raf = 0
    const onMove = (event: PointerEvent) => {
      const rect = el.getBoundingClientRect()
      const px = (event.clientX - rect.left) / rect.width - 0.5
      const py = (event.clientY - rect.top) / rect.height - 0.5
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        el.style.setProperty('--tilt-x', `${(-py * 7).toFixed(2)}deg`)
        el.style.setProperty('--tilt-y', `${(px * 9).toFixed(2)}deg`)
        el.style.setProperty('--glare-x', `${((px + 0.5) * 100).toFixed(1)}%`)
        el.style.setProperty('--glare-y', `${((py + 0.5) * 100).toFixed(1)}%`)
      })
    }
    const reset = () => {
      cancelAnimationFrame(raf)
      el.style.setProperty('--tilt-x', '0deg')
      el.style.setProperty('--tilt-y', '0deg')
    }
    el.addEventListener('pointermove', onMove)
    el.addEventListener('pointerleave', reset)
    ;(el as HTMLElement & { _fxOff?: () => void })._fxOff = () => {
      el.removeEventListener('pointermove', onMove)
      el.removeEventListener('pointerleave', reset)
    }
  },
  unmounted(el) {
    ;(el as HTMLElement & { _fxOff?: () => void })._fxOff?.()
  },
}

/** v-countup: animate the leading number of the element's text when it scrolls into view. */
export const vCountup: Directive<HTMLElement> = {
  mounted(el) {
    const original = el.textContent ?? ''
    const match = original.match(/^([^0-9]*)([\d,.]+)(.*)$/s)
    if (!match || reduced()) return
    const [, prefix, digits, suffix] = match
    // Only a quantity counts up; a date or range ("Oct 2–17", "10.2–10.17") keeps its text.
    if (/[\p{L}]/u.test(prefix) || /\d/.test(suffix)) return
    const target = Number(digits.replace(/,/g, ''))
    if (!Number.isFinite(target) || target === 0) return
    const decimals = digits.includes('.') ? digits.split('.')[1].length : 0
    const grouped = digits.includes(',')
    // The real value stays in place until the animation actually starts, so a page that is
    // never scrolled to it (a screenshot, a background tab) never shows a placeholder 0.
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      observer.disconnect()
      el.textContent = `${prefix}${(0).toFixed(decimals)}${suffix}`
      const started = performance.now()
      const duration = 1100
      const frame = (now: number) => {
        const progress = Math.min(1, (now - started) / duration)
        const eased = 1 - (1 - progress) ** 3
        let value = (target * eased).toFixed(decimals)
        if (grouped) value = Number(value).toLocaleString('en-US', { minimumFractionDigits: decimals })
        el.textContent = `${prefix}${value}${suffix}`
        if (progress < 1) requestAnimationFrame(frame)
        else el.textContent = original
      }
      requestAnimationFrame(frame)
    }, { threshold: 0.4 })
    observer.observe(el)
  },
}
