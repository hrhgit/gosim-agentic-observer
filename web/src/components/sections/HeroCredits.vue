<script setup lang="ts">
// One line under the hero actions: organizer and sponsor (linking to their sites) and the scientific committee (linking to /about).
import { useI18n } from '../../composables/useI18n'
import { appUrl } from '../../composables/api'
import { useOrganizers } from '../../composables/useOrganizers'

const { t } = useI18n()
const { items, logo, wordmark, site } = useOrganizers()
</script>

<template>
  <div class="hero-credits" data-testid="hero-credits" :aria-label="t('home.credits.aria')" role="group">
    <a v-for="item in items" :key="item.name" class="hero-credit" :href="site(item.name) || undefined" target="_blank" rel="noopener">
      <span class="hero-credit-role">{{ item.role }}</span>
      <img v-if="logo(item.name)" :src="logo(item.name)" :alt="wordmark(item.name) ? item.name : ''" :title="item.name" class="hero-credit-logo" :class="{ wordmark: wordmark(item.name) }">
      <b v-if="!wordmark(item.name)">{{ item.name }}</b>
    </a>
    <a class="hero-credit" href="https://github.com/octos-org/" target="_blank" rel="noopener" data-testid="hero-credits-octos">
      <span class="hero-credit-role">{{ t('home.credibility.octos.kicker') }}</span>
      <img :src="appUrl('media/octos-logo.webp')" :alt="t('home.credibility.octos.logoAlt')" class="hero-credit-logo wordmark">
      <b>Octos</b>
    </a>
    <router-link :to="{ path: '/about', hash: '#committee' }" class="hero-credit hero-credit-committee" data-testid="hero-credits-all">
      <b>{{ t('home.credibility.committee.kicker') }}</b><span aria-hidden="true">→</span>
    </router-link>
  </div>
</template>

<style scoped>
.hero-credits {
  display: flex; flex-wrap: wrap; align-items: center; gap: 1rem 2.75rem;
  font-size: 1.35rem; line-height: 1.3; color: rgba(226,234,255,.92);
}
.hero-credit {
  display: inline-flex; align-items: center; gap: .8rem; min-width: 0;
  color: inherit; text-decoration: none; transition: opacity .2s ease;
}
a.hero-credit:hover { opacity: .8; }
.hero-credit:focus-visible { outline: 1px solid #78a6ff; outline-offset: 6px; }
.hero-credit-role {
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .95rem;
  letter-spacing: .1em; text-transform: uppercase; color: rgba(255,255,255,.66);
}
.hero-credit b { font-weight: 700; font-size: 1.75rem; color: #f5f7ff; letter-spacing: .01em; }
.hero-credit-logo { height: 44px; width: 44px; border-radius: 6px; }
.hero-credit-logo.wordmark { height: 38px; width: auto; border-radius: 0; }
.hero-credit-committee span { color: #78a6ff; font-size: 1.6rem; }
.hero-credit-committee:hover span { color: #f7f9ff; }
@media (max-width: 640px) {
  .hero-credits { gap: .75rem 1.5rem; font-size: 1.1rem; }
  .hero-credit-role { font-size: .78rem; }
  .hero-credit b { font-size: 1.35rem; }
  .hero-credit-logo { height: 34px; width: 34px; }
  .hero-credit-logo.wordmark { height: 28px; }
}
</style>
