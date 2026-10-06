<script setup lang="ts">
import { registrationUrl, platformLinks } from '../../registration'
import EventSwitcher from './EventSwitcher.vue'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '../../composables/useI18n'

const { t, pick, toggleLocale } = useI18n()
const router = useRouter()
const mobileOpen = ref(false)
const base = import.meta.env.BASE_URL

type NavItem = { key: string; to?: string; href?: string }

const items: NavItem[] = [
  { key: 'nav.vision', to: '/brief' },
  { key: 'nav.about', href: '#about' },
  { key: 'nav.mission', href: '#mission' },
  { key: 'nav.participate', href: '#participate' },
  { key: 'nav.leaderboard', href: '#leaderboard' },
]

function scrollTo(hash: string) {
  mobileOpen.value = false
  if (router.currentRoute.value.path !== '/') {
    router.push({ path: '/', hash })
    return
  }
  document.querySelector(hash)?.scrollIntoView({ behavior: 'smooth' })
}
</script>

<template>
  <header class="cosmos-header sticky top-0 z-50 border-b border-border backdrop-blur">
    <div class="gosim-nav-inner flex items-center justify-between gap-6">
      <div class="gosim-brand-switcher">
      <a href="https://create.gosim.org/" aria-label="GOSIM Create home" class="flex items-center gap-3">
        <span class="create-brand"><img :src="`${base}gosim-logo.svg`" alt="GOSIM"><span>Create</span></span>
      </a>
        <span class="gosim-brand-divider" aria-hidden="true"></span>
        <EventSwitcher current="/survey26/" :english="pick('en', 'zh') === 'en'" />
      </div>

      <nav class="hidden items-center gap-5 xl:flex">
        <router-link
          v-for="item in items.filter(i => i.to)"
          :key="item.to"
          :to="item.to!"
          class="inline-flex h-10 items-center font-mono text-xs uppercase tracking-[.06em] text-white/50 transition-colors hover:text-[#edb28b]"
        >{{ t(item.key) }}</router-link>
        <a
          v-for="item in items.filter(i => i.href)"
          :key="item.href"
          :href="item.href"
          @click.prevent="scrollTo(item.href!)"
          class="inline-flex h-10 items-center font-mono text-xs uppercase tracking-[.06em] text-white/50 transition-colors hover:text-[#edb28b]"
        >{{ t(item.key) }}</a>
        <a :href="platformLinks.login" target="_blank" rel="noopener noreferrer" class="inline-flex h-10 items-center whitespace-nowrap font-mono text-xs text-[#edb28b]">{{ pick('Log in', '登录') }} ↗</a>
      </nav>

      <div class="flex items-center gap-2">
        <button @click="toggleLocale" class="inline-flex h-10 min-w-12 items-center justify-center border border-white/25 px-2 font-mono text-xs uppercase text-white/55 transition-colors hover:border-white/60 hover:text-white">
          {{ pick('中文', 'EN') }}
        </button>
        <a :href="registrationUrl" target="_blank" rel="noopener noreferrer" class="cosmos-register-link ml-1 hidden shrink-0 whitespace-nowrap h-10 items-center border px-4 font-mono text-xs font-semibold uppercase tracking-widest md:inline-flex">{{ pick('Register now', '立即报名') }}</a>
        <button class="ml-1 xl:hidden" @click="mobileOpen = !mobileOpen" :aria-label="pick('Menu', '菜单')">
          <svg class="h-6 w-6 text-text-primary" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
        </button>
      </div>
    </div>

    <div v-if="mobileOpen" class="border-t border-white/20 bg-[#18242f] px-5 py-4 xl:hidden">
      <router-link
        v-for="item in items.filter(i => i.to)"
        :key="item.to"
        :to="item.to!"
        @click="mobileOpen = false"
        class="block border-b border-white/10 py-3 text-base text-white/60 transition-colors hover:text-white"
      >{{ t(item.key) }}</router-link>
      <a
        v-for="item in items.filter(i => i.href)"
        :key="item.href"
        :href="item.href"
        @click.prevent="scrollTo(item.href!)"
        class="block border-b border-white/10 py-3 text-base text-white/60 transition-colors hover:text-white"
      >{{ t(item.key) }}</a>
      <a :href="platformLinks.login" target="_blank" rel="noopener noreferrer" class="block py-3 text-base text-[#edb28b]">{{ pick('Log in', '登录') }} ↗</a>
      <a :href="registrationUrl" target="_blank" rel="noopener noreferrer" class="cosmos-register-link mt-3 block border px-4 py-3 text-center font-mono text-xs font-semibold uppercase tracking-widest">
        {{ pick('Register now', '立即报名') }}
      </a>
    </div>
  </header>
</template>

<style scoped>
.create-brand { display: inline-flex; align-items: center; gap: 9px; flex-shrink: 0; white-space: nowrap; color: #f0e9dd; font: 500 21px/1 'Manrope', sans-serif; letter-spacing: -.035em; }
.create-brand img { width: 76px; height: 24px; }
@media (min-width: 1024px) and (max-width: 1279px) { nav { gap: 12px; } }
</style>

<style scoped>
.gosim-nav-inner { box-sizing: border-box; width: 100%; max-width: 1280px; height: 64px; margin-inline: auto; padding-inline: 24px; }
@media (max-width: 760px) { .gosim-nav-inner { padding-inline: 16px; } }
</style>

<style scoped>
.gosim-brand-switcher { display: flex; align-items: center; gap: 12px; flex-shrink: 0; color: var(--color-text-primary); }
.gosim-brand-divider { height: 20px; width: 1px; background: currentColor; opacity: .25; }
@media (max-width: 639px) { .gosim-brand-switcher { gap: 6px; } .gosim-nav-inner { gap: 8px; } }
</style>
