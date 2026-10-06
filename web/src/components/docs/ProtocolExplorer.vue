<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'
import sample from '../../content/demo/protocol-sample.json'

const { t } = useI18n()
type Tab = 'initialize' | 'decision_request' | 'decision_response'
const tabs: Tab[] = ['initialize', 'decision_request', 'decision_response']
const active = ref<Tab>('initialize')
const copied = ref(false)
const pretty = (value: unknown) => JSON.stringify(value, null, 2)
const code = computed(() => {
  if (active.value === 'initialize') return pretty(sample.initialize)
  if (active.value === 'decision_request') return pretty(sample.decision_request)
  return `${pretty(sample.decision_response_observe)}\n\n${pretty(sample.decision_response_wait)}`
})
async function copy() {
  try { await navigator.clipboard.writeText(code.value); copied.value = true; window.setTimeout(() => { copied.value = false }, 1600) }
  catch { /* clipboard unavailable: nothing to do */ }
}
</script>

<template>
  <section class="protocol-panel" data-testid="protocol-explorer" :aria-label="t('docs_page.protocol.title')">
    <div class="protocol-head">
      <span class="label">{{ t('docs_page.protocol.title') }}</span>
      <span class="text3 font-mono text-xs uppercase tracking-[.1em]">participant-agent-protocol-v2 · JSON lines · stdin / stdout</span>
    </div>
    <div class="tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab" type="button" role="tab" :aria-selected="active === tab" :class="{ active: active === tab }" :data-testid="`protocol-tab-${tab}`" @click="active = tab">{{ t(`docs_page.protocol.tabs.${tab}`) }}</button>
    </div>
    <p class="text2 mt-4 text-sm">{{ t(`docs_page.protocol.desc.${active}`) }}</p>
    <div class="relative mt-3">
      <button type="button" class="copy-btn protocol-copy" :aria-label="t('common.copy')" @click="copy">{{ copied ? t('common.copied') : t('common.copy') }}</button>
      <pre class="code-block protocol-code" tabindex="0">{{ code }}</pre>
    </div>
    <p class="text3 mt-3 text-xs">{{ t('docs_page.protocol.truncated') }}</p>
  </section>
</template>

<style scoped>
.protocol-panel { border: 1px solid rgba(255,255,255,.2); padding: 1.25rem; margin-bottom: 3rem; background: rgba(6,6,7,.6); }
.protocol-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem 1rem; margin-bottom: .75rem; }
.protocol-copy { position: absolute; top: .6rem; right: .6rem; background: #0d0d0d; }
.protocol-code { max-height: 26rem; margin: 0; }
</style>
