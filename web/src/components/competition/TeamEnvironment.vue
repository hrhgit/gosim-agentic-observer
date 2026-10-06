<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { portal, type TeamEnvironment } from '../../lib/observerPortal'
import { PROVIDERS, configuredServices, newVariableCount, normalizePrefix, presetById, serviceNames, serviceVariableNames,
  serviceWrites, type ConfiguredService } from '../../lib/modelServices'

// Keys and network: the team's variables (environment of its program). With open
// egress the program may reach any public address, so there is no domain list;
// otherwise (rollback) the domains it may reach on port 443. Secret values are write-only.
const props = defineProps<{ environment: TeamEnvironment | null | undefined; busy: boolean }>()
const emit = defineEmits<{ act: [work: () => Promise<void>, success: string] }>()
const { t, tf } = useI18n()
const variables = computed(() => props.environment?.variables ?? [])
const domains = computed(() => props.environment?.domains ?? [])
const open = computed(() => props.environment?.open === true)
const limits = computed(() => props.environment?.limits ?? { variables: 20, domains: 10, value_bytes: 8192 })
const form = ref({ name: '', value: '', secret: true })
const domain = ref('')
// 出网线路 (egress route): labels only; the platform keeps the route details.
const route = computed(() => props.environment?.egress_route ?? null)
const ROUTES = ['direct', 'cn', 'overseas'] as const
function setRoute(name: string, autoFallback?: boolean) {
  const body: Record<string, unknown> = { route: name }
  if (autoFallback !== undefined) body.auto_fallback = autoFallback
  emit('act', async () => { await portal('set_team_egress_route', body) }, t('submit.team_env.route.saved'))
}

// "Add a model service": provider preset -> key (secret) + base URL + model (plain values).
const services = computed(() => configuredServices(variables.value))
const existing = computed(() => new Set(variables.value.map(v => v.name)))
const svc = ref({ provider: PROVIDERS[0].id, key: '', baseUrl: PROVIDERS[0].baseUrl, model: PROVIDERS[0].model, prefix: '' })
const editing = ref<string | null>(null)
const replaceOk = ref<string | null>(null)
const preset = computed(() => presetById(svc.value.provider))
const target = computed(() => serviceNames(preset.value.protocol, svc.value.prefix))
const targetPrefix = computed(() => target.value.key.replace(/_API_KEY$/, ''))
// Asked only once a key is being entered (or a service edited), not as a warning on an untouched form.
const clash = computed(() => (svc.value.key.trim() || editing.value)
  ? services.value.find(s => s.names.key === target.value.key && s.prefix !== editing.value) ?? null : null)
const suggestedPrefix = computed(() => {
  for (let n = 1; n < 20; n++) {
    const p = n === 1 ? preset.value.prefix : `${preset.value.prefix}${n}`
    if (!existing.value.has(`${p}_API_KEY`)) return p
  }
  return preset.value.prefix
})
const writes = computed(() => serviceWrites(svc.value, existing.value))
const overLimit = computed(() => !!writes.value && variables.value.length + newVariableCount(writes.value, existing.value) > limits.value.variables)
const canSave = computed(() => !!writes.value && !overLimit.value && (!clash.value || replaceOk.value === targetPrefix.value))
const providerName = (id: string) => id === 'custom' ? t('submit.team_env.svc.custom') : presetById(id).label

function chooseProvider() {
  svc.value.baseUrl = preset.value.baseUrl
  svc.value.model = preset.value.model
  replaceOk.value = null
}
function resetService() {
  svc.value = { provider: PROVIDERS[0].id, key: '', baseUrl: PROVIDERS[0].baseUrl, model: PROVIDERS[0].model, prefix: '' }
  editing.value = null; replaceOk.value = null
}
function editService(s: ConfiguredService) {
  const provider = s.provider?.id ?? 'custom'
  const isDefault = s.prefix === (presetById(provider).protocol === 'anthropic' ? 'ANTHROPIC' : 'OPENAI')
  svc.value = { provider, key: '', baseUrl: s.baseUrl || presetById(provider).baseUrl, model: s.model, prefix: isDefault ? '' : s.prefix }
  editing.value = s.prefix; replaceOk.value = null
  document.querySelector('[data-testid="model-service-form"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
function saveService() {
  const list = writes.value
  if (!list || !canSave.value) return
  // The key never stays in the page once it has been sent.
  svc.value.key = ''
  emit('act', async () => {
    for (const w of list) {
      if (w.op === 'save') await portal('save_team_variable', { name: w.name, value: w.value, secret: w.secret, model: w.model })
      else await portal('delete_team_variable', { name: w.name })
    }
    resetService()
  }, t('submit.team_env.svc.saved'))
}
function deleteService(s: ConfiguredService) {
  const names = [s.names.key, s.names.baseUrl, s.names.model].filter(n => existing.value.has(n))
  if (!window.confirm(tf('submit.team_env.svc.delete_confirm', { names: names.join(', ') }))) return
  emit('act', async () => {
    for (const name of names) await portal('delete_team_variable', { name })
    if (editing.value === s.prefix) resetService()
  }, t('submit.team_env.svc.deleted'))
}

// Switch a variable off (kept, not given to runs) or tag it as model-related (left out of
// evaluations started with 「本次不提供模型」).
function setFlags(name: string, flags: { model?: boolean; disabled?: boolean }) {
  emit('act', async () => { await portal('set_team_variable_flags', { name, ...flags }) },
    flags.disabled === undefined ? t('submit.team_env.flags.model_saved') : flags.disabled ? t('submit.team_env.flags.disabled') : t('submit.team_env.flags.enabled'))
}
function toggleService(s: ConfiguredService) {
  const names = serviceVariableNames(s, existing.value)
  const disabled = !s.disabled
  emit('act', async () => { for (const name of names) await portal('set_team_variable_flags', { name, disabled }) },
    disabled ? t('submit.team_env.flags.disabled') : t('submit.team_env.flags.enabled'))
}

function saveVariable() {
  const body = { name: form.value.name.trim().toUpperCase(), value: form.value.value, secret: form.value.secret }
  // The value never stays in the page once it has been sent.
  form.value.value = ''
  emit('act', async () => { await portal('save_team_variable', body); form.value.name = ''; form.value.secret = true },
    t('submit.team_env.saved'))
}
function deleteVariable(name: string) {
  emit('act', async () => { await portal('delete_team_variable', { name }) }, t('submit.team_env.deleted'))
}
function setDomains(list: string[], clear = false) {
  emit('act', async () => { await portal('set_team_domains', { domains: list }); if (clear) domain.value = '' },
    t('submit.team_env.domains_saved'))
}
function addDomain() {
  const host = domain.value.trim().toLowerCase().replace(/^https?:\/\//, '').replace(/[/:].*$/, '')
  if (host && !domains.value.includes(host)) setDomains([...domains.value, host], true)
}
</script>

<template>
  <div data-testid="team-environment">
    <p class="help mt-3">{{ open ? t('submit.team_env.intro_open') : t('submit.team_env.intro') }}</p>
    <p v-if="environment?.relay_key_missing" class="errors" role="alert" data-testid="team-env-relay-banner">{{ t('submit.team_env.relay_banner') }}</p>
    <p class="help" data-testid="team-env-final-note">{{ t('submit.team_env.final_note') }}</p>

    <section class="svc-panel mt-4" data-testid="model-service">
      <h3>{{ services.length ? t('submit.team_env.svc.title_more') : t('submit.team_env.svc.title') }}</h3>
      <p class="help">{{ t('submit.team_env.svc.intro') }}</p>
      <p class="help" data-testid="model-service-examples">{{ t('submit.team_env.svc.examples') }}</p>

      <ul v-if="services.length" class="svc-cards" data-testid="model-service-cards">
        <li v-for="s in services" :key="s.prefix" class="svc-card" :class="{ editing: editing === s.prefix, off: s.disabled }" :data-testid="'model-service-card-' + s.prefix">
          <div class="min-w-0">
            <strong>{{ s.provider ? providerName(s.provider.id) : s.label }}</strong>
            <span v-if="s.disabled" class="pill ml-2" :data-testid="'model-service-off-' + s.prefix">{{ t('submit.team_env.flags.off_pill') }}</span>
            <span> · {{ s.model || t('submit.team_env.svc.no_model') }}</span>
            <span> · {{ s.hint ? tf('submit.team_env.svc.key_ending', { hint: s.hint }) : t('submit.model_api.key_hidden') }}</span>
            <div class="help break-all"><code>{{ s.prefix }}_*</code> <span v-if="s.baseUrl">{{ s.baseUrl }}</span></div>
          </div>
          <span class="svc-actions">
            <button type="button" class="btn sm" :disabled="busy" :data-testid="'model-service-edit-' + s.prefix" @click="editService(s)">{{ t('submit.team_env.svc.edit') }}</button>
            <button type="button" class="btn sm" :disabled="busy" :title="t('submit.team_env.flags.service_help')" :data-testid="'model-service-toggle-' + s.prefix" @click="toggleService(s)">{{ s.disabled ? t('submit.team_env.flags.enable') : t('submit.team_env.flags.disable') }}</button>
            <button type="button" class="btn sm danger" :disabled="busy" :data-testid="'model-service-delete-' + s.prefix" @click="deleteService(s)">{{ t('submit.team_env.delete') }}</button>
          </span>
        </li>
      </ul>

      <form class="mt-3" autocomplete="off" data-testid="model-service-form" @submit.prevent="saveService">
        <p v-if="editing" class="help">{{ tf('submit.team_env.svc.editing', { prefix: editing }) }}</p>
        <label class="field"><span>{{ t('submit.team_env.svc.provider') }}</span>
          <select v-model="svc.provider" name="observer-service-provider" data-testid="model-service-provider" @change="chooseProvider">
            <option v-for="p in PROVIDERS" :key="p.id" :value="p.id">{{ providerName(p.id) }}</option>
          </select>
        </label>
        <p v-if="preset.note" class="help svc-note" data-testid="model-service-note">{{ t('submit.team_env.svc.notes.' + preset.note) }}</p>
        <label class="field"><span>{{ t('submit.team_env.svc.key') }}</span><input v-model="svc.key" type="password" name="observer-service-key" :required="!editing" maxlength="8192" :placeholder="editing ? t('submit.team_env.svc.key_keep') : 'sk-…'" autocomplete="new-password" spellcheck="false" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-testid="model-service-key"></label>
        <label class="field"><span>{{ t('submit.team_env.svc.base_url') }}</span><input v-model="svc.baseUrl" type="url" name="observer-service-base-url" required pattern="https://.+" maxlength="1000" placeholder="https://api.example.com/v1" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-testid="model-service-base-url"></label>
        <label class="field"><span>{{ t('submit.team_env.svc.model') }}</span><input v-model="svc.model" type="text" name="observer-service-model" maxlength="200" :placeholder="preset.placeholder" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-testid="model-service-model"></label>
        <p v-if="!svc.model.trim()" class="help">{{ t('submit.team_env.svc.model_empty') }}</p>
        <label class="field"><span>{{ t('submit.team_env.svc.prefix') }}</span><input v-model="svc.prefix" type="text" name="observer-service-prefix" maxlength="40" :placeholder="preset.prefix" autocomplete="off" spellcheck="false" data-testid="model-service-prefix" @blur="svc.prefix = normalizePrefix(svc.prefix)"></label>
        <p class="help">{{ t('submit.team_env.svc.prefix_help') }}</p>
        <p class="help" data-testid="model-service-target">{{ t('submit.team_env.svc.saves_as') }} <code>{{ target.key }}</code> · <code>{{ target.baseUrl }}</code> · <code>{{ target.model }}</code></p>
        <div v-if="clash && replaceOk !== targetPrefix" class="svc-clash" role="alert" data-testid="model-service-clash">
          <p>{{ tf('submit.team_env.svc.clash', { prefix: targetPrefix, current: clash.label + (clash.model ? ' · ' + clash.model : '') }) }}</p>
          <p class="svc-actions mt-2">
            <button type="button" class="btn sm" data-testid="model-service-replace" @click="replaceOk = targetPrefix">{{ tf('submit.team_env.svc.replace', { prefix: targetPrefix }) }}</button>
            <button type="button" class="btn sm" data-testid="model-service-use-prefix" @click="svc.prefix = suggestedPrefix">{{ tf('submit.team_env.svc.use_prefix', { prefix: suggestedPrefix }) }}</button>
          </p>
        </div>
        <p v-if="overLimit" class="errors">{{ tf('submit.team_env.svc.over_limit', { n: limits.variables }) }}</p>
        <p class="svc-actions">
          <button class="btn sm primary" :disabled="busy || !canSave" data-testid="model-service-save">{{ t('submit.team_env.svc.save') }}</button>
          <button v-if="editing" type="button" class="btn sm" @click="resetService">{{ t('submit.team_env.svc.cancel') }}</button>
        </p>
      </form>
    </section>

    <details class="mt-4 svc-advanced" :open="!services.length" data-testid="team-env-advanced">
    <summary><h3 class="inline">{{ t('submit.team_env.svc.advanced') }}</h3></summary>
    <h3 class="mt-3">{{ t('submit.team_env.vars_title') }}</h3>
    <p class="help">{{ tf('submit.team_env.vars_help', { variables: limits.variables }) }}</p>
    <p class="help" data-testid="team-env-flags-help">{{ t('submit.team_env.flags.help') }}</p>
    <ul v-if="variables.length" class="team-env-list" data-testid="team-env-variables">
      <li v-for="v in variables" :key="v.name" :class="{ off: v.disabled }" :data-testid="'team-env-variable-' + v.name">
        <code>{{ v.name }}</code>
        <span class="help break-all">{{ v.secret ? (v.hint ? tf('submit.model_api.key_ending', { hint: v.hint }) : t('submit.model_api.key_hidden')) : v.value }}</span>
        <span v-if="v.disabled" class="pill" :data-testid="'team-env-off-' + v.name">{{ t('submit.team_env.flags.off_pill') }}</span>
        <label v-if="v.disabled !== undefined" class="check flag"><input type="checkbox" :checked="!v.disabled" :disabled="busy" :data-testid="'team-env-enabled-' + v.name"
          @change="setFlags(v.name, { disabled: !($event.target as HTMLInputElement).checked })">{{ t('submit.team_env.flags.enabled_label') }}</label>
        <label v-if="v.model !== undefined" class="check flag" :title="t('submit.team_env.flags.model_help')"><input type="checkbox" :checked="v.model" :disabled="busy" :data-testid="'team-env-model-' + v.name"
          @change="setFlags(v.name, { model: ($event.target as HTMLInputElement).checked })">{{ t('submit.team_env.flags.model_label') }}</label>
        <button type="button" class="btn sm" :disabled="busy" :data-testid="'team-env-delete-' + v.name" @click="deleteVariable(v.name)">{{ t('submit.team_env.delete') }}</button>
      </li>
    </ul>
    <p v-else class="help">{{ t('submit.team_env.no_variables') }}</p>
    <form class="mt-3" autocomplete="off" data-testid="team-env-variable-form" @submit.prevent="saveVariable">
      <label class="field"><span>{{ t('submit.team_env.name') }}</span><input v-model="form.name" type="text" name="observer-variable-name" required maxlength="64" pattern="[A-Za-z][A-Za-z0-9_]{0,63}" placeholder="OPENAI_API_KEY" autocomplete="off" spellcheck="false" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-testid="team-env-name"></label>
      <label class="field"><span>{{ t('submit.team_env.value') }}</span><input v-model="form.value" :type="form.secret ? 'password' : 'text'" name="observer-variable-value" required maxlength="8192" autocomplete="new-password" spellcheck="false" data-lpignore="true" data-1p-ignore="true" data-bwignore="true" data-form-type="other" data-testid="team-env-value"></label>
      <label class="check"><input v-model="form.secret" type="checkbox" name="observer-variable-secret" data-testid="team-env-secret">{{ t('submit.team_env.secret') }}</label>
      <p><button class="btn sm" :disabled="busy || variables.length >= limits.variables && !variables.some(v => v.name === form.name.trim().toUpperCase())" data-testid="team-env-save">{{ t('submit.team_env.save') }}</button></p>
    </form>
    </details>

    <template v-if="!open">
    <h3 class="mt-4">{{ t('submit.team_env.domains_title') }}</h3>
    <p class="help">{{ tf('submit.team_env.domains_help', { domains: limits.domains }) }}</p>
    <ul v-if="domains.length" class="team-env-list" data-testid="team-env-domains">
      <li v-for="host in domains" :key="host">
        <code>{{ host }}</code>
        <button type="button" class="btn sm" :disabled="busy" :data-testid="'team-env-remove-' + host" @click="setDomains(domains.filter(d => d !== host))">{{ t('submit.team_env.remove') }}</button>
      </li>
    </ul>
    <p v-else class="help">{{ t('submit.team_env.no_domains') }}</p>
    <form class="mt-3" autocomplete="off" data-testid="team-env-domain-form" @submit.prevent="addDomain">
      <label class="field"><span>{{ t('submit.team_env.domain') }}</span><input v-model="domain" type="text" name="observer-domain" required maxlength="253" placeholder="api.kimi.com" autocomplete="off" spellcheck="false" data-testid="team-env-domain"></label>
      <p><button class="btn sm" :disabled="busy || domains.length >= limits.domains" data-testid="team-env-add-domain">{{ t('submit.team_env.add_domain') }}</button></p>
    </form>
    <p class="help mt-3">{{ t('submit.team_env.proxy_note') }}</p>
    </template>
    <template v-else>
    <h3 class="mt-4">{{ t('submit.team_env.network_title') }}</h3>
    <p class="help" data-testid="team-env-network-open">{{ t('submit.team_env.network_open') }}</p>
    <p class="help">{{ t('submit.team_env.network_log') }}</p>
    <section v-if="route?.available" class="mt-4" data-testid="team-env-route">
      <h3>{{ t('submit.team_env.route.title') }}</h3>
      <p class="help">{{ t('submit.team_env.route.intro') }}</p>
      <div class="route-options" role="radiogroup" :aria-label="t('submit.team_env.route.title')">
        <label v-for="name in ROUTES" :key="name" class="check">
          <input type="radio" name="observer-egress-route" :value="name" :checked="route.route === name" :disabled="busy"
                 :data-testid="'team-env-route-' + name" @change="setRoute(name)">
          {{ t('submit.team_env.route.' + name) }}
        </label>
      </div>
      <label v-if="route.route !== 'direct'" class="check">
        <input type="checkbox" name="observer-egress-route-fallback" :checked="route.auto_fallback" :disabled="busy"
               data-testid="team-env-route-fallback" @change="setRoute(route.route, ($event.target as HTMLInputElement).checked)">
        {{ t('submit.team_env.route.fallback') }}
      </label>
      <p class="help">{{ t('submit.team_env.route.note') }}</p>
    </section>
    </template>
  </div>
</template>

<style scoped>
.team-env-list { list-style: none; padding: 0; margin: .5rem 0; display: grid; gap: .4rem; }
.team-env-list li { display: flex; flex-wrap: wrap; align-items: center; gap: .6rem; }
.team-env-list code { font-weight: 600; }
.team-env-list li.off code, .svc-card.off strong { opacity: .55; text-decoration: line-through; }
.check.flag { font-size: .85rem; margin: 0; }
.svc-panel { border: 1px solid rgba(158,173,255,.35); padding: 1rem 1.1rem; background: rgba(49,94,251,.06); }
.svc-panel select { width: 100%; }
.svc-cards { list-style: none; padding: 0; margin: .75rem 0; display: grid; gap: .5rem; }
.svc-card { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .6rem; padding: .6rem .75rem; border: 1px solid rgba(158,173,255,.22); }
.svc-card.editing { border-color: #78a6ff; }
.svc-actions { display: flex; flex-wrap: wrap; gap: .5rem; }
.svc-note { color: #f3d58a; margin: -.6rem 0 1rem; }
.svc-clash { border: 1px solid #b88a2a; padding: .6rem .75rem; margin-bottom: 1rem; }
.svc-advanced > summary { cursor: pointer; }
.route-options { display: flex; flex-wrap: wrap; gap: .4rem 1.2rem; margin: .5rem 0; }
</style>
