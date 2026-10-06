<script setup lang="ts">
import { registrationUrl } from '../../registration'
import { useI18n } from '../../composables/useI18n'
import heroImage from '../../assets/images/cosmos-observatory-hero.jpg'
const { t, pick } = useI18n()
</script>

<template>
  <section class="poster-section poster-canvas py-8 md:py-12">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="cta-poster reveal relative min-h-[650px] overflow-hidden border border-white/20 p-6 md:min-h-[760px] md:p-10 lg:p-14">
        <img :src="heroImage" alt="" loading="lazy">
        <div class="cta-plasma plasma-field" aria-hidden="true"></div>
        <div class="cta-overlay" aria-hidden="true"></div>

        <div class="relative z-10 flex min-h-[590px] flex-col justify-between md:min-h-[680px]">
          <div class="flex justify-between gap-6 border-b border-white/30 pb-5 font-mono text-xs uppercase tracking-[.1em] text-white/60">
            <span>2026 / GOSIM / AGENT OBSERVER</span>
            <span>{{ pick('Final transmission', '最终传输') }}</span>
          </div>

          <div>
            <h2 class="max-w-[16ch] text-[clamp(2.25rem,4.6vw,4.75rem)] font-semibold leading-[1.04] tracking-[-.05em] text-[#f0e9dd]">{{ t('cta.title') }}</h2>
            <div class="mt-9 grid gap-8 border-t border-white/30 pt-7 md:grid-cols-[1fr_auto] md:items-end">
              <div>
                <p class="max-w-2xl text-base leading-relaxed text-white/75 md:text-lg">{{ t('cta.tagline') }}</p>
                <p class="mt-4 font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ t('cta.location') }}</p>
              </div>
              <a :href="registrationUrl" target="_blank" rel="noopener noreferrer" class="inline-flex min-w-60 items-center bg-[#f0e9dd] px-5 py-3 font-mono text-xs font-semibold uppercase tracking-widest text-black">
                {{ pick('Register now', '立即报名') }}
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.cta-poster { background: #18242f; box-shadow: 8px 8px 0 #edb28b; }
.cta-poster > img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 56% center; filter: grayscale(1) contrast(1.25) brightness(.64); }
.cta-overlay { position: absolute; z-index: 1; inset: 0; background: linear-gradient(90deg, rgba(16,29,41,.93) 0%, rgba(16,29,41,.72) 46%, rgba(16,29,41,.16) 100%), linear-gradient(0deg, rgba(16,29,41,.88), transparent 60%); }
.cta-plasma { z-index: 2; right: -8%; top: 28%; transform: rotate(-12deg); }
@media (max-width: 720px) {
  .cta-poster > img { object-position: 65% center; }
  .cta-overlay { background: linear-gradient(90deg, rgba(16,29,41,.9), rgba(16,29,41,.48)), linear-gradient(0deg, rgba(16,29,41,.92), transparent 65%); }
  .cta-plasma { right: -42%; top: 26%; }
}
</style>
