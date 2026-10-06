<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { supabase } from '../../lib/supabase'
import { describeError, extractMessage } from '../../lib/errors'
import { loadKimiPlanStatus } from '../../lib/data'
import { kimiPlanSoldOutAfterClaim, kimiPlanState, normalizeKimiPlanStatus, type KimiPlanStatus } from '../../lib/kimiPlan'
import { fmtUtc } from '../../lib/format'
import { useAuth } from '../../stores/auth'
import { useFlash } from '../../stores/flash'
const i18n = useI18n()
const { t, tf } = i18n
const flash = useFlash()
const { team } = useAuth()
const ERROR_NS = ['kimi_plan.errors', 'credits.errors']
const MASK = '••••••'

const status = ref<KimiPlanStatus>(normalizeKimiPlanStatus(null))
const loading = ref(true)
const busy = ref(false)
const revealed = ref(false)
const copied = ref(false)
const state = computed(() => kimiPlanState(status.value))
const redeemUrl = computed(() => status.value.code ? `https://www.kimi.com?invite=okc&code=${encodeURIComponent(status.value.code)}` : '')
const redeemUrlDisplay = computed(() => status.value.code ? `https://www.kimi.com?invite=okc&code=${revealed.value ? status.value.code : MASK}` : '')

async function load() {
  if (!team.value) { loading.value = false; return }
  try { status.value = await loadKimiPlanStatus() }
  catch (e) { flash.error(describeError(e, i18n, ERROR_NS)) }
  finally { loading.value = false }
}

async function claim() {
  busy.value = true
  try {
    const { data, error } = await supabase.rpc('claim_kimi_plan_code')
    if (error) throw error
    flash.success(t((data as { already?: boolean } | null)?.already ? 'kimi_plan.already' : 'kimi_plan.claimed'))
    status.value = await loadKimiPlanStatus()
    revealed.value = true
  } catch (e) {
    // Pool ran out since the status loaded: show the calm sold-out notice, not an error.
    const soldOut = kimiPlanSoldOutAfterClaim(status.value, extractMessage(e))
    if (soldOut) status.value = soldOut
    else flash.error(describeError(e, i18n, ERROR_NS))
  }
  finally { busy.value = false }
}

async function copy() {
  if (!status.value.code) return
  try { await navigator.clipboard.writeText(status.value.code); copied.value = true; window.setTimeout(() => { copied.value = false }, 2000) } catch { /* clipboard unavailable */ }
}

onMounted(load)
</script>

<template>
  <div v-if="team" id="kimi-plan" class="panel" data-testid="kimi-plan-panel" :data-state="loading ? 'loading' : state">
    <div class="hd"><h2>{{ t('kimi_plan.title') }}</h2><span class="label">{{ t('kimi_plan.kicker') }}</span></div>
    <p class="text3 mb-2 text-xs" data-testid="kimi-plan-sponsor">{{ t('kimi_plan.sponsor') }}</p>
    <p class="text2 text-sm">{{ t('kimi_plan.lede') }}</p>
    <p v-if="loading" class="text3 mt-3 text-sm">{{ t('common.loading') }}</p>
    <template v-else>
      <p v-if="state === 'coming_soon'" class="text2 mt-3 text-sm" data-testid="kimi-plan-coming">{{ t('kimi_plan.coming_soon') }}</p>
      <p v-if="state === 'sold_out'" class="text2 mt-3 text-sm" data-testid="kimi-plan-sold-out">{{ t('kimi_plan.sold_out') }}</p>
      <p v-if="state !== 'claimed' && state !== 'sold_out'" class="mt-2 text-sm" :class="status.eligible ? 'accent-l' : 'text3'" data-testid="kimi-plan-eligibility">
        {{ status.eligible ? t('kimi_plan.eligible') : status.qualified && status.hidden ? t('kimi_plan.hidden') : t('kimi_plan.not_eligible') }}
      </p>
      <p v-if="state === 'wait_captain'" class="text2 mt-2 text-sm">{{ t('kimi_plan.wait_captain') }}</p>
      <div v-else-if="state === 'claimable'" class="mt-3">
        <button type="button" class="btn sm primary" data-testid="kimi-plan-claim" :disabled="busy" @click="claim">{{ busy ? t('common.working') : t('kimi_plan.claim') }} →</button>
      </div>
      <div v-else-if="state === 'claimed' && status.code" class="mt-3">
        <div class="actions-inline">
          <code class="credits-code" :class="{ masked: !revealed }" data-testid="kimi-plan-code">{{ revealed ? status.code : MASK }}</code>
          <button type="button" class="copy-btn" :aria-pressed="revealed" @click="revealed = !revealed">{{ revealed ? t('credits.hide') : t('credits.reveal') }}</button>
          <button type="button" class="copy-btn" @click="copy">{{ copied ? t('common.copied') : t('common.copy') }}</button>
        </div>
        <a v-if="redeemUrl" class="btn sm primary mt-3" data-testid="kimi-plan-redeem" :href="redeemUrl" target="_blank" rel="noopener">{{ t('kimi_plan.redeem') }} →</a>
        <p v-if="redeemUrl" class="text3 mt-2 text-xs break-all">{{ redeemUrlDisplay }}</p>
        <p v-else-if="status.note" class="text3 mt-2 text-xs">{{ status.note }}</p>
        <p class="text3 mt-2 text-xs">{{ tf('kimi_plan.claimed_by', { name: status.claimed_by ?? '—', at: fmtUtc(status.claimed_at, { short: true }) }) }}</p>
      </div>
    </template>
  </div>
</template>
