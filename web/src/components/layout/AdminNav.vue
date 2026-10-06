<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
const route = useRoute()
const items = [
  { to: '/admin', key: 'admin.nav.overview' },
  { to: '/admin/phases', key: 'admin.nav.phases' },
  { to: '/admin/scenarios', key: 'admin.nav.scenarios' },
  { to: '/admin/submissions', key: 'admin.nav.submissions' },
  { to: '/admin/incidents', key: 'admin.nav.incidents' },
  { to: '/admin/teams', key: 'admin.nav.teams' },
  { to: '/admin/users', key: 'admin.nav.users' },
  { to: '/admin/announcements', key: 'admin.nav.announcements' },
  { to: '/admin/credits', key: 'admin.nav.credits' },
  { to: '/admin/settings', key: 'admin.nav.settings' },
]
const isActive = (to: string) => to === '/admin' ? route.path === '/admin' : route.path.startsWith(to)
</script>

<template>
  <nav class="admin-nav">
    <router-link v-for="item in items" :key="item.to" :to="item.to" :class="{ active: isActive(item.to) }">{{ t(item.key) }}</router-link>
  </nav>
</template>
