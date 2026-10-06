<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'

const { t } = useI18n()
const route = useRoute()
const open = ref(false)
const KEY = 'cosmos-construction-ack'

onMounted(() => {
  try { open.value = window.sessionStorage.getItem(KEY) !== '1' } catch { open.value = true }
})

function dismiss() {
  open.value = false
  try { window.sessionStorage.setItem(KEY, '1') } catch {}
}
</script>

<template>
  <div v-if="open && route.path !== '/register'" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 px-6" @click.self="dismiss">
    <div class="w-full max-w-md border border-border bg-bg-card p-8">
      <span class="mono-label text-accent">{{ t('nav.applyNow') }}</span>
      <h2 class="mt-4 text-2xl font-semibold tracking-[-.03em] text-text-primary">{{ t('construction.title') }}</h2>
      <p class="mt-4 text-sm leading-relaxed text-text-secondary">{{ t('construction.desc') }}</p>
      <button @click="dismiss" class="mt-7 w-full bg-accent px-5 py-3 text-xs font-semibold uppercase tracking-[.14em] text-[#18242f] transition-colors hover:bg-accent-hover">
        {{ t('construction.ok') }}
      </button>
    </div>
  </div>
</template>
