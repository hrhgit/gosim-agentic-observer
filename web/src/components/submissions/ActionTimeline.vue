<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
import { actionNet, OUTCOME_COLORS, outcomeClass, type OutcomeClass, type ReportAction } from '../../lib/report'
import { fmtUtc, num } from '../../lib/format'

const props = defineProps<{ actions: ReportAction[] }>()
const { t } = useI18n()
const CLASSES: OutcomeClass[] = ['completed', 'wait', 'interrupted', 'unsafe', 'invalid']
/**
 * Past this many actions the 1px gap and 1px minimum per bar no longer fit any column (a 180-night run has
 * thousands), so bars drop both and share the width by weight alone.
 */
const DENSE = 120

/** Waits are squeezed so that exposures stay visible even in runs that idle for most of the calendar. */
const weightOf = (a: ReportAction) => Math.max(1, Number(a.elapsed_seconds) || 1) * (a.action === 'wait' ? 0.15 : 1)
const bars = computed(() => {
  const total = props.actions.reduce((s, a) => s + weightOf(a), 0) || 1
  return props.actions.map(a => ({ a, cls: outcomeClass(a.outcome, a.action), width: `${((weightOf(a) / total) * 100).toFixed(3)}%` }))
})
const counts = computed(() => {
  const out: Record<OutcomeClass, number> = { completed: 0, wait: 0, interrupted: 0, unsafe: 0, invalid: 0, report: 0 }
  for (const b of bars.value) out[b.cls]++
  return out
})
function tooltip(a: ReportAction): string {
  const head = `${a.decision_id} · ${a.slot_id} · ${a.action} ${a.tile_id || ''} ${a.program || ''}${a.request_id ? ` · ${a.request_id}` : ''}\n${a.outcome} · ${fmtUtc(a.start_utc, { seconds: true })} UTC · ${a.elapsed_seconds}s · ${num(actionNet(a), 1)}`
  const segs = (a.segments ?? []).map(s => `  ${s.slot_id} ${s.duration_seconds}s · airmass ${num(s.airmass, 2)} · atm ${num(s.atmospheric_quality, 3)} × lunar ${num(s.lunar_quality_factor, 2)} = ${num(s.combined_quality, 3)} → ${s.quality_band}${s.program_matched ? ' ✓' : ''} · ${num(s.base_science_score, 1)}+${num(s.program_bonus_score, 1)}${s.active_event_ids?.length ? ` · events ${s.active_event_ids.join(',')}` : ''}`)
  return segs.length ? `${head}\n${segs.join('\n')}` : head
}
</script>

<template>
  <div class="action-timeline" data-testid="action-timeline">
    <div class="timeline mt-3" :class="{ dense: bars.length > DENSE }">
      <i v-for="b in bars" :key="b.a.decision_id" :style="{ flex: `0 1 ${b.width}`, background: OUTCOME_COLORS[b.cls] }" :title="tooltip(b.a)"></i>
    </div>
    <div class="legend">
      <span v-for="c in CLASSES" :key="c"><i :style="{ background: OUTCOME_COLORS[c] }"></i>{{ t(`subs.outcome_class.${c}`) }} <b class="text-text-primary">{{ counts[c] }}</b></span>
    </div>
  </div>
</template>
