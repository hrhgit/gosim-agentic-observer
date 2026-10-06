<script setup lang="ts">
// Personal API tokens for the survey26 command-line tool. Shown only while the rollout
// switch (private.cli_config.enabled) includes this account, or when the account
// still has tokens it may want to revoke. The token itself is shown once, right after
// it is created; the server keeps only its hash.
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { supabase } from '../../lib/supabase'
import { formatDateTime } from '../../lib/projectText'
import { useFlash } from '../../stores/flash'

type Token = { id: string; name: string; hint: string; created_at: string; last_used_at: string | null }
const { pick, locale } = useI18n()
const flash = useFlash()
const enabled = ref(false), limit = ref(5), tokens = ref<Token[]>([]), loaded = ref(false)
const name = ref(''), busy = ref(false), created = ref<{ name: string; token: string } | null>(null), copied = ref(false)
const w = computed(() => pick({
  title: 'Personal API tokens', kicker: 'Command line',
  lede: 'For the survey26 command-line tool and coding agents. A token acts as you, with exactly your permissions, daily limits and quotas; organizer functions are not available with a token. Anyone who has a token can act as you: keep it out of repositories, logs and chats, and revoke it when you no longer need it.',
  guide: 'Command-line guide', name: 'Token name (e.g. laptop, agent)', create: 'Create token', working: 'Working…',
  once: 'Copy this token now. It is shown only once; the website keeps only a fingerprint of it.', copy: 'Copy', copied: 'Copied',
  done: 'I have stored it', empty: 'No tokens yet.', created: 'Created', used: 'Last used', never: 'never',
  revoke: 'Revoke', revokeConfirm: 'Revoke this token? Programs using it stop working immediately.', revoked: 'Token revoked.',
  limitHint: 'At most {n} active tokens.',
  errors: { token_limit: 'You already have the maximum number of active tokens. Revoke one first.', invalid_token_name: 'Enter a name of 1–60 characters.',
    cli_tokens_disabled: 'API tokens are not available right now.', account_banned: 'This account is suspended.', token_not_found: 'This token no longer exists.' } as Record<string, string>,
  failed: 'This request could not be completed. Refresh and try again.',
}, {
  title: '个人 API 令牌', kicker: '命令行',
  lede: '供 survey26 命令行工具和编程智能体使用。令牌以你的身份操作，权限、每日次数和配额与网站完全相同；令牌不能使用主办方功能。任何拿到令牌的人都能以你的身份操作：请勿把令牌写进仓库、日志或聊天记录，不再使用时请及时撤销。',
  guide: '命令行使用指南', name: '令牌名称（如 laptop、agent）', create: '创建令牌', working: '处理中…',
  once: '请立即复制这个令牌。它只显示这一次，网站只保存它的指纹。', copy: '复制', copied: '已复制',
  done: '我已保存', empty: '还没有令牌。', created: '创建于', used: '最近使用', never: '从未使用',
  revoke: '撤销', revokeConfirm: '撤销这个令牌？使用它的程序会立即失效。', revoked: '令牌已撤销。',
  limitHint: '最多 {n} 个有效令牌。',
  errors: { token_limit: '有效令牌已达上限，请先撤销一个。', invalid_token_name: '请输入 1–60 个字符的名称。',
    cli_tokens_disabled: 'API 令牌功能暂未开放。', account_banned: '该账号已被停用。', token_not_found: '这个令牌已不存在。' } as Record<string, string>,
  failed: '操作未完成，请刷新后重试。',
}))
const when = (value: string | null) => value ? formatDateTime(value, locale.value) : w.value.never
const errorText = (e: unknown) => {
  const code = e && typeof e === 'object' && 'message' in e ? String((e as { message: unknown }).message) : ''
  return w.value.errors[code] ?? w.value.failed
}

async function load() {
  const { data, error } = await supabase.rpc('my_cli_tokens')
  // A database without the feature simply shows nothing.
  if (!error && data) { enabled.value = !!data.enabled; limit.value = Number(data.limit) || 5; tokens.value = data.tokens ?? [] }
  loaded.value = true
}
async function create() {
  if (busy.value) return
  busy.value = true
  try {
    const { data, error } = await supabase.rpc('create_cli_token', { p_name: name.value.trim() })
    if (error) throw error
    created.value = { name: data.name, token: data.token }; copied.value = false; name.value = ''
    await load()
  } catch (e) { flash.error(errorText(e)) }
  finally { busy.value = false }
}
async function revoke(token: Token) {
  if (busy.value || !window.confirm(w.value.revokeConfirm)) return
  busy.value = true
  try {
    const { error } = await supabase.rpc('revoke_cli_token', { p_id: token.id })
    if (error) throw error
    flash.success(w.value.revoked)
    await load()
  } catch (e) { flash.error(errorText(e)) }
  finally { busy.value = false }
}
async function copy() {
  if (!created.value) return
  try { await navigator.clipboard.writeText(created.value.token); copied.value = true } catch { /* select the text manually */ }
}
onMounted(() => { void load().catch(() => { loaded.value = true }) })
</script>

<template>
  <div v-if="loaded && (enabled || tokens.length)" id="api-tokens" class="panel mt-6" data-testid="api-tokens-panel">
    <div class="hd"><h2>{{ w.title }}</h2><span class="label">{{ w.kicker }}</span></div>
    <p class="text2 text-sm">{{ w.lede }} <RouterLink to="/cli" class="accent-l">{{ w.guide }} →</RouterLink></p>
    <div v-if="created" class="mt-4" data-testid="api-token-created">
      <p class="text-sm"><strong>{{ created.name }}</strong> · {{ w.once }}</p>
      <div class="actions-inline mt-2">
        <code class="credits-code break-all" data-testid="api-token-value">{{ created.token }}</code>
        <button type="button" class="copy-btn" @click="copy">{{ copied ? w.copied : w.copy }}</button>
      </div>
      <button type="button" class="btn sm mt-3" @click="created = null">{{ w.done }}</button>
    </div>
    <form v-if="enabled" class="mt-4" @submit.prevent="create" novalidate>
      <label class="field"><span>{{ w.name }}</span><input v-model="name" data-testid="api-token-name" type="text" maxlength="60" required></label>
      <button class="btn sm primary" type="submit" data-testid="api-token-create" :disabled="busy || !name.trim() || tokens.length >= limit">{{ busy ? w.working : w.create }}</button>
      <small class="help ml-2">{{ w.limitHint.replace('{n}', String(limit)) }}</small>
    </form>
    <p v-if="!tokens.length" class="text3 mt-4 text-sm">{{ w.empty }}</p>
    <div v-else class="table-wrap mt-4">
      <table class="data-table">
        <tbody>
          <tr v-for="token in tokens" :key="token.id" data-testid="api-token-row">
            <td>{{ token.name }} <span class="m text3 text-xs">s26_…{{ token.hint }}</span></td>
            <td class="text-xs text3">{{ w.created }} {{ when(token.created_at) }}<br>{{ w.used }} {{ when(token.last_used_at) }}</td>
            <td class="r"><button type="button" class="copy-btn whitespace-nowrap" :disabled="busy" @click="revoke(token)">{{ w.revoke }}</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
