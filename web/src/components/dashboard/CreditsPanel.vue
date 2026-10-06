<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { supabase } from '../../lib/supabase'
import { describeError } from '../../lib/errors'
import { loadCreditsNote, loadMyRedeemCodes, loadRedeemProviders, type RedeemCode, type RedeemProvider } from '../../lib/data'
import { useAuth } from '../../stores/auth'
import { useFlash } from '../../stores/flash'
import SkeletonRows from '../layout/SkeletonRows.vue'

const i18n = useI18n()
const { t, tf, locale } = i18n
const flash = useFlash()
const { team } = useAuth()
const ERROR_NS = ['credits.errors']
const MASK = '••••••'

const providers = ref<RedeemProvider[]>([])
const codes = ref<Record<string, RedeemCode>>({})
const noteText = ref({ en: '', zh: '' })
const revealed = ref<Record<string, boolean>>({})
const copied = ref('')
const loading = ref(true)
const busy = ref('')

const note = computed(() => (locale.value === 'zh' ? noteText.value.zh : noteText.value.en).trim())

async function load() {
  if (!team.value) { loading.value = false; return }
  try {
    const [list, mine, text] = await Promise.all([loadRedeemProviders(), loadMyRedeemCodes(), loadCreditsNote()])
    providers.value = list
    codes.value = Object.fromEntries(mine.map(c => [c.provider, c]))
    noteText.value = text
  } catch (e) { flash.error(describeError(e, i18n, ERROR_NS)) }
  finally { loading.value = false }
}

async function claim(provider: string) {
  busy.value = provider
  try {
    const { data, error } = await supabase.rpc('claim_redeem_code', { p_provider: provider })
    if (error) throw error
    const row = (data ?? {}) as { provider?: string; code?: string; note?: string; already?: boolean }
    codes.value = { ...codes.value, [provider]: { provider, code: String(row.code ?? ''), note: String(row.note ?? ''), assigned_at: null } }
    flash.success(t(row.already ? 'credits.already' : 'credits.claimed'))
    providers.value = await loadRedeemProviders()
  } catch (e) { flash.error(describeError(e, i18n, ERROR_NS)) }
  finally { busy.value = '' }
}

function toggle(provider: string) { revealed.value = { ...revealed.value, [provider]: !revealed.value[provider] } }

async function copy(provider: string) {
  const entry = codes.value[provider]
  if (!entry) return
  try { await navigator.clipboard.writeText(entry.code); copied.value = provider; window.setTimeout(() => { copied.value = '' }, 2000) } catch { /* clipboard unavailable */ }
}

onMounted(load)
</script>

<template>
  <div class="panel" data-testid="credits-panel">
    <div class="hd"><h2>{{ t('credits.title') }}</h2><span class="label">{{ t('credits.kicker') }}</span></div>
    <p v-if="!team" class="text2">{{ t('credits.need_team') }}</p>
    <template v-else>
      <p class="text2 text-sm">{{ note || t('credits.lede') }}</p>
      <SkeletonRows v-if="loading" :rows="2" :cols="3" :label="t('credits.loading')" class="mt-3" />
      <p v-else-if="!providers.length" class="text3 mt-4 text-sm">{{ t('credits.empty') }}</p>
      <ul v-else class="credits-list mt-3">
        <li v-for="p in providers" :key="p.provider" class="credits-row" :data-testid="`credits-row-${p.provider}`">
          <div class="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span class="provider">{{ p.provider }}</span>
            <span class="label">{{ tf('credits.available', { n: p.available }) }}</span>
          </div>
          <div v-if="codes[p.provider]" class="mt-3">
            <div class="actions-inline">
              <code class="credits-code" :class="{ masked: !revealed[p.provider] }" :data-testid="`credits-code-${p.provider}`">{{ revealed[p.provider] ? codes[p.provider].code : MASK }}</code>
              <button type="button" class="copy-btn" :data-testid="`credits-reveal-${p.provider}`" :aria-pressed="Boolean(revealed[p.provider])" @click="toggle(p.provider)">{{ revealed[p.provider] ? t('credits.hide') : t('credits.reveal') }}</button>
              <button type="button" class="copy-btn" :data-testid="`credits-copy-${p.provider}`" @click="copy(p.provider)">{{ copied === p.provider ? t('common.copied') : t('common.copy') }}</button>
            </div>
            <p v-if="codes[p.provider].note" class="text3 mt-2 text-xs">{{ codes[p.provider].note }}</p>
          </div>
          <div v-else class="mt-3">
            <button type="button" class="btn sm" :class="{ primary: p.available > 0 }" :data-testid="`credits-claim-${p.provider}`" :disabled="busy !== '' || p.available <= 0" @click="claim(p.provider)">{{ busy === p.provider ? t('common.working') : t('credits.claim') }} →</button>
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>
