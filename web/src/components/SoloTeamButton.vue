<script setup lang="ts">
// One click to compete alone: a one-person team named after the participant, then on to Participate.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { randomToken } from '../lib/storage'
import { describeError } from '../lib/errors'
import { createSoloTeam } from '../lib/soloTeam'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { loadCompetition } from '../stores/competition'
import { useTeamCapacity } from '../composables/useTeamCapacity'
import { teamCreationBlocked } from '../lib/teamCapacity'

withDefaults(defineProps<{ primary?: boolean }>(), { primary: false })
const i18n = useI18n()
const { t, tf } = i18n
const flash = useFlash()
const { me, refreshMe } = useAuth()
const route = useRoute()
const router = useRouter()
const busy = ref(false)
const { capacity, reload: reloadCapacity } = useTeamCapacity()
const blocked = computed(() => teamCreationBlocked(capacity.value, me.value?.is_admin))

async function goSolo() {
  if (busy.value) return
  busy.value = true
  try {
    const profile = me.value ?? await refreshMe()
    if (!profile) throw new Error('not_authenticated')
    const name = await createSoloTeam(profile, async candidate => {
      const { error } = await supabase.rpc('create_team', { p_name: candidate, p_max_size: 1, p_project_idea: '', p_github_repo: '' })
      if (error) throw error
    }, () => randomToken(4))
    await refreshMe()
    await loadCompetition(true)
    flash.success(tf('team.solo_done', { name }))
    if (route.path !== '/compete') await router.push('/compete')
  } catch (error) {
    flash.error(describeError(error, i18n, ['team.errors', 'team']))
    // Another tab may have joined a team meanwhile; show whatever is true now.
    void refreshMe()
  } finally {
    void reloadCapacity()
    busy.value = false
  }
}
</script>

<template>
  <button type="button" class="btn sm" :class="{ primary }" :disabled="busy || blocked" :aria-busy="busy" data-testid="solo-team" @click="goSolo">
    {{ busy ? t('common.working') : t('team.solo') }}
  </button>
</template>
