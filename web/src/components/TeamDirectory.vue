<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from '../composables/useI18n'
import { useAuth } from '../stores/auth'
import { supabase } from '../lib/supabase'
import { teamAction } from '../stores/teamNotifications'
import { describeError } from '../lib/errors'
import { directoryCounts, filterTeams, isRecruiting, type DirectoryTeam } from '../lib/teamDirectory'
import SoloTeamButton from './SoloTeamButton.vue'

const PAGE = 20
const i18n = useI18n()
const { t, tf } = i18n
const { team, isLoggedIn } = useAuth()
const entries = ref<DirectoryTeam[]>([]), loading = ref(true), busy = ref(''), error = ref('')
const sent = ref(new Set<string>())
// Recruiting teams by default; full or paused ones only on request.
const query = ref(''), showAll = ref(false), limit = ref(PAGE)
const counts = computed(() => directoryCounts(entries.value))
const matches = computed(() => filterTeams(entries.value, query.value, showAll.value))
const shown = computed(() => matches.value.slice(0, limit.value))
watch([query, showAll], () => { limit.value = PAGE })
const canRequest = (entry: DirectoryTeam) => isLoggedIn.value && !team.value && isRecruiting(entry) && !sent.value.has(entry.id)

onMounted(async () => {
  const result = await supabase.rpc('team_directory')
  if (result.error) error.value = describeError(result.error, i18n)
  else entries.value = result.data ?? []
  if (isLoggedIn.value) {
    const {data} = await supabase.rpc('my_team_invitations')
    for (const item of data ?? []) if (item.direction==='sent' && item.kind==='request' && item.status==='pending') sent.value.add(item.team_id)
  }
  loading.value = false
})
async function request(entry: DirectoryTeam) {
  if (busy.value || sent.value.has(entry.id)) return
  busy.value = entry.id; error.value = ''
  try { await teamAction('request_team_join', { p_team_id: entry.id }); sent.value.add(entry.id) }
  catch (e) { error.value = describeError(e, i18n, ['team.errors', 'team']) }
  finally { busy.value = '' }
}
</script>

<template>
  <section id="teams" class="panel mt-8" data-testid="team-directory">
    <div class="hd"><h2>{{ t('team.directory.title') }}</h2><span v-if="!loading" class="label">{{ tf('team.directory.recruiting', { n: counts.recruiting }) }}</span></div>
    <p class="text2 text-sm mb-4">{{ t('team.directory.lede') }}</p>
    <div class="team-dir-tools">
      <input v-model="query" type="search" class="input" :placeholder="t('team.directory.search')" :aria-label="t('team.directory.search')" data-testid="team-directory-search">
      <label class="check"><input v-model="showAll" type="checkbox" data-testid="team-directory-all"> {{ tf('team.directory.show_all', { n: counts.all }) }}</label>
    </div>
    <p v-if="error" role="alert" class="errors">{{ error }}</p>
    <p v-if="loading" role="status">{{ t('common.loading') }}</p>
    <div v-else-if="!matches.length" class="team-dir-empty" data-testid="team-directory-empty">
      <p class="text2">{{ t('team.directory.empty') }}</p>
      <p v-if="!team" class="actions-inline mt-3">
        <template v-if="isLoggedIn">
          <router-link class="btn sm" :to="{ path: '/team', hash: '#create' }">{{ t('team.directory.create_own') }} →</router-link>
          <SoloTeamButton />
        </template>
        <router-link v-else class="btn sm" :to="{ path: '/team', hash: '#create' }">{{ t('team.directory.create_own') }} / {{ t('team.directory.go_solo') }} →</router-link>
      </p>
    </div>
    <ul v-else class="team-dir-list">
      <li v-for="entry in shown" :key="entry.id" class="team-dir-row" :data-team-id="entry.id">
        <div class="team-dir-name">
          <button v-if="canRequest(entry)" type="button" class="team-dir-link" :title="entry.name" :disabled="!!busy" @click="request(entry)">{{ entry.name }}</button>
          <strong v-else :title="entry.name">{{ entry.name }}</strong>
          <span class="team-dir-size">{{ entry.member_count }} / {{ entry.max_size }}</span>
        </div>
        <div class="team-dir-action">
          <span v-if="team?.id === entry.id" class="text3 text-sm">{{ t('team.directory.yours') }}</span>
          <span v-else-if="entry.is_locked" class="text3 text-sm">{{ t('team.directory.paused') }}</span>
          <span v-else-if="entry.member_count >= entry.max_size" class="text3 text-sm">{{ t('team.directory.full') }}</span>
          <router-link v-else-if="sent.has(entry.id)" class="copy-btn" to="/notifications">{{ t('team.directory.requested') }}</router-link>
          <router-link v-else-if="!isLoggedIn" class="btn sm" :to="{path:'/register',query:{mode:'login',next:'/teammates#teams'}}">{{ t('team.directory.sign_in') }}</router-link>
          <button v-else-if="!team" type="button" class="btn sm" :disabled="!!busy" @click="request(entry)">{{ busy === entry.id ? t('common.working') : t('team.directory.request') }}</button>
          <span v-else class="text3 text-sm">{{ t('team.directory.in_team') }}</span>
        </div>
      </li>
    </ul>
    <button v-if="matches.length > shown.length" type="button" class="copy-btn mt-4" @click="limit += PAGE">{{ tf('team.directory.more', { n: Math.min(PAGE, matches.length - shown.length) }) }}</button>
  </section>
</template>

<style scoped>
.team-dir-tools { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem 1.25rem; margin-bottom: 1rem; }
.team-dir-tools .input { flex: 1 1 14rem; width: auto; padding: .55rem .8rem; font-size: .95rem; }
.team-dir-tools .check { margin: 0; white-space: nowrap; }
.team-dir-list { margin: 0; padding: 0; list-style: none; }
/* One row per team: the name gives way (ellipsis), the size and the action never move. */
.team-dir-row { display: flex; align-items: center; gap: .75rem; min-height: 3.25rem; padding: .5rem 0; border-bottom: 1px solid rgba(255,255,255,.08); }
.team-dir-name { display: flex; flex: 1 1 auto; align-items: baseline; gap: .6rem; min-width: 0; }
.team-dir-name > button, .team-dir-name > strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; color: #f5f5f5; }
.team-dir-link { padding: 0; border: 0; background: none; text-align: left; text-decoration: underline; text-underline-offset: 4px; }
.team-dir-link:hover:not(:disabled) { color: #78a6ff; }
.team-dir-size { flex: none; font-size: .85rem; color: #858585; font-variant-numeric: tabular-nums; white-space: nowrap; }
.team-dir-action { flex: none; margin-left: auto; text-align: right; white-space: nowrap; }
/* Very narrow screens: every action drops below its name, the same way on every row. */
@media (max-width: 359px) {
  .team-dir-row { flex-wrap: wrap; row-gap: .4rem; }
  .team-dir-action { flex-basis: 100%; text-align: left; }
}
.team-dir-empty { padding: 1rem 0 .25rem; }
</style>
