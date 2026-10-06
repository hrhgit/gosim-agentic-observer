<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
const props = defineProps<{ current: string; english: boolean }>()
const open = ref(false)
const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const events = [
  { href: '/agenticapp26/', name: 'Agentic App 黑客松', en: 'Agentic App', detail: 'Shenzhen 2026 · Agentic App' },
  { href: '/factory26/', name: '智能体工厂国际黑客松与大奖赛', en: 'Agentic Factory', detail: 'Shenzhen 2026 · OAIC' },
  { href: '/survey26/', name: '巡天智能体', en: 'Agent Observer', detail: 'Shenzhen 2026 · Agent Observer' },
]
const labels: Record<string, string[]> = {
  '/factory26/': ['智能体工厂', 'Agentic Factory'],
  '/survey26/': ['巡天智能体', 'Agent Observer'],
}

function dismiss(event: PointerEvent) {
  if (event.target instanceof Node && !root.value?.contains(event.target)) open.value = false
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) {
    event.stopPropagation()
    open.value = false
    trigger.value?.focus()
  }
}
function focusOut(event: FocusEvent) {
  if (!(event.relatedTarget instanceof Node) || !root.value?.contains(event.relatedTarget)) open.value = false
}
onMounted(() => document.addEventListener('pointerdown', dismiss))
onUnmounted(() => document.removeEventListener('pointerdown', dismiss))
</script>

<template>
  <div ref="root" class="event-switcher" @keydown="escape" @focusout="focusOut">
    <button ref="trigger" class="series-trigger" type="button" :aria-expanded="open" aria-controls="event-series-links" :aria-label="props.english ? 'Switch hackathon' : '切换黑客松'" @click="open = !open">
      <span class="series-label">{{ labels[props.current]?.[props.english ? 1 : 0] }}</span><svg :class="{ expanded: open }" width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.5"/></svg>
    </button>
    <div v-show="open" id="event-series-links" class="series-panel" :aria-label="props.english ? 'Hackathons' : '选择黑客松'">
      <div class="panel-label"><span>{{ props.english ? 'Shenzhen Hackathon Series' : '深圳黑客松系列' }}</span><span>{{ props.english ? 'Switch event' : '选择赛事' }}</span></div>
      <a v-for="(event, index) in events" :key="event.href" :href="event.href" :aria-current="event.href === props.current ? 'page' : undefined" @click="open = false">
        <span class="event-index" aria-hidden="true">0{{ index + 1 }}</span><span class="event-name">{{ props.english ? event.en : event.name }}<small v-if="event.href === props.current">{{ props.english ? 'Current' : '当前' }}</small></span>
        <span class="event-detail">{{ event.detail }}</span>
      </a>
    </div>
  </div>
</template>

<style scoped>
.event-switcher { position: relative; flex: none; }
.series-trigger { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 0; border: 0; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.series-trigger svg { flex: none; transition: transform .2s; }
.series-trigger svg.expanded { transform: rotate(180deg); }
.series-label { font-size: 12px; white-space: nowrap; }
.series-panel { position: absolute; top: calc(100% + 12px); left: 0; width: 330px; max-width: calc(100vw - 32px); background: var(--color-bg-primary, #18242f); color: var(--color-text-primary, #f0e9dd); border: 1px solid var(--color-border, #41515c); box-shadow: 0 18px 40px #0003; z-index: 60; }
.panel-label { display: flex; justify-content: space-between; padding: 14px 16px; font-size: 11px; border-bottom: 1px solid var(--color-border, #41515c); }
.series-panel a { position: relative; display: block; padding: 18px 16px 18px 42px; text-decoration: none; color: inherit; border-left: 2px solid transparent; }
.series-panel a + a { border-top: 1px solid var(--color-border, #41515c); }
.series-panel a:hover, .series-panel a:focus-visible { background: color-mix(in srgb, currentColor 9%, transparent); }
.series-panel a[aria-current] { border-left-color: currentColor; background: color-mix(in srgb, currentColor 6%, transparent); }
.event-index { position: absolute; left: 12px; top: 21px; font-size: 11px; opacity: .65; }
.event-name { display: flex; align-items: baseline; gap: 10px; justify-content: space-between; font-size: 13px; line-height: 1.6; }
.event-name small { white-space: nowrap; font-size: 11px; }
.event-detail { display: block; opacity: .65; font-size: 11px; line-height: 1.6; margin-top: 5px; }
@media (max-width: 760px) { .series-panel { position: fixed; top: 64px; left: 16px; } }
@media (max-width: 639px) { .series-label { display: none; } .series-trigger { width: 32px; justify-content: center; } }
</style>
