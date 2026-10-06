<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { pendingFriendRequests, pendingTeamActions } from '../../stores/teamNotifications'
const { t, pick } = useI18n()
const route = useRoute()
const items = [
  { to: '/dashboard', key: 'dash.title', exact: true },
  { to: '/team', key: 'nav.team' },
  { to: '/compete', key: 'nav.submit', exact: true },
  { to: '/submissions', key: 'nav.submissions' },
  { to: '/profile', key: 'nav.profile', exact: true },
]
const isActive = (item: { to: string; exact?: boolean }) => item.exact ? route.path === item.to : route.path.startsWith(item.to)
</script>

<template>
  <nav class="admin-nav">
    <router-link v-for="item in items" :key="item.to" :to="item.to" :class="{ active: isActive(item) }">{{ t(item.key) }}<span v-if="item.to === '/team' && pendingTeamActions"
      class="nav-count" data-testid="dash-nav-team-count" :title="pick('Requests waiting for your answer', '待你处理的组队请求')">{{ pendingTeamActions > 99 ? '99+' : pendingTeamActions }}</span><span v-if="item.to === '/profile' && pendingFriendRequests"
      class="nav-count" data-testid="dash-nav-friend-count" :title="pick('Friend requests waiting for your answer', '待你处理的好友请求')">{{ pendingFriendRequests > 99 ? '99+' : pendingFriendRequests }}</span></router-link>
  </nav>
</template>

<style scoped>
.nav-count { display: inline-flex; align-items: center; justify-content: center; min-width: 1.1rem; height: 1.1rem; margin-left: .35rem; padding: 0 .3rem;
  border-radius: 999px; background: #ef4444; color: #fff; font-size: .65rem; line-height: 1; vertical-align: .1em; }
</style>
