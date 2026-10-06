<script setup lang="ts">
import { useI18n } from '../../composables/useI18n'
import heroImage from '../../assets/images/cosmos-observatory-hero.jpg'
import heroImageAvif from '../../assets/images/cosmos-observatory-hero.avif'
import heroImageWebp from '../../assets/images/cosmos-observatory-hero.webp'
import { useAuth } from '../../stores/auth'
import { useRegistrationOpen } from '../../composables/useRegistrationOpen'
const { t, pick } = useI18n()
const { isLoggedIn } = useAuth()
const { registrationOpen } = useRegistrationOpen()
</script>

<template>
  <section class="poster-section poster-canvas py-8 md:py-12">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="cta-poster reveal reveal-scale relative min-h-[650px] overflow-hidden border border-white/20 p-6 md:min-h-[760px] md:p-10 lg:p-14">
        <picture>
          <source :srcset="heroImageAvif" type="image/avif">
          <source :srcset="heroImageWebp" type="image/webp">
          <img :src="heroImage" alt="" loading="lazy" width="1821" height="864">
        </picture>
        <div class="cta-plasma plasma-field" aria-hidden="true"></div>
        <div class="cta-scan" aria-hidden="true"></div>
        <div class="cta-overlay" aria-hidden="true"></div>

        <div class="relative z-10 flex min-h-[590px] flex-col justify-between md:min-h-[680px]">
          <div class="flex justify-between gap-6 border-b border-white/30 pb-5 font-mono text-xs uppercase tracking-[.1em] text-white/60 reveal">
            <span>{{ t('cta.tag') }}</span>
            <span>{{ pick('Final transmission', '最终传输') }}</span>
          </div>

          <div>
            <h2 class="max-w-[16ch] text-[clamp(2.25rem,4.6vw,4.75rem)] font-semibold leading-[1.04] tracking-[-.05em] text-[#f5f5f5] reveal reveal-delay-1">{{ t('cta.title') }}</h2>
            <div class="mt-9 grid gap-8 border-t border-white/30 pt-7 md:grid-cols-[1fr_auto] md:items-end reveal reveal-delay-2">
              <div>
                <p class="max-w-2xl text-base leading-relaxed text-white/75 md:text-lg">{{ t('cta.tagline') }}</p>
                <p class="mt-4 font-mono text-xs uppercase tracking-[.1em] text-[#315efb]">{{ t('cta.location') }}</p>
              </div>
              <router-link v-if="isLoggedIn" to="/compete" class="btn light min-w-60">{{ t('dash.new_submission') }} →</router-link>
              <router-link v-else-if="registrationOpen" to="/register" class="btn light min-w-60">{{ t('cta.button') }} →</router-link>
              <span v-else class="btn light disabled min-w-60">{{ t('nav.registration_closed') }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.cta-poster { background: radial-gradient(circle at 20% 20%, rgba(49,94,251,.2), transparent 28%), #050506; box-shadow: 8px 8px 0 #315efb; }
.cta-poster > picture { position: absolute; inset: 0; display: block; }
.cta-poster > picture > img { width: 100%; height: 100%; object-fit: cover; object-position: 56% center; filter: grayscale(1) contrast(1.25) brightness(.64); transform: scale(1.04); transition: transform 6s ease; }
.cta-poster:hover > picture > img { transform: scale(1.08); }
.cta-overlay { position: absolute; z-index: 1; inset: 0; background: linear-gradient(90deg, rgba(5,5,6,.93) 0%, rgba(5,5,6,.72) 46%, rgba(5,5,6,.16) 100%), linear-gradient(0deg, rgba(5,5,6,.88), transparent 60%); }
.cta-scan { position: absolute; z-index: 2; inset: 0; pointer-events: none; background: linear-gradient(180deg, transparent, rgba(120,166,255,.08), transparent); transform: translateY(-100%); animation: scan 7s linear infinite; }
@keyframes scan { to { transform: translateY(100%); } }
.cta-plasma { z-index: 2; right: -8%; top: 28%; transform: rotate(-12deg); }
@media (prefers-reduced-motion: reduce) {
  .cta-scan { animation: none; }
  .cta-poster > picture > img { transform: none; transition: none; }
}

@media (max-width: 720px) {
  .cta-poster > picture > img { object-position: 65% center; }
  .cta-overlay { background: linear-gradient(90deg, rgba(5,5,6,.9), rgba(5,5,6,.48)), linear-gradient(0deg, rgba(5,5,6,.92), transparent 65%); }
  .cta-plasma { right: -42%; top: 26%; }
}
</style>
