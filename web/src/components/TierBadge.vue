<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'

const props = defineProps<{ kind: 'astro' | 'ai'; level: number | null | undefined; short?: boolean }>()
const { t } = useI18n()
const level = computed(() => Math.min(3, Math.max(0, Math.round(Number(props.level ?? 0)))))
const names = computed(() => t(`tiers.${props.kind}`) as string[])
const label = computed(() => names.value[level.value] ?? names.value[0])
</script>

<template>
  <span class="tier-badge" :class="`tier-${kind}-${level}`" :title="`${t(kind === 'astro' ? 'tiers.astro_label' : 'tiers.ai_label')} · ${label}`">
    <span class="tier-icon" aria-hidden="true">{{ kind === 'astro' ? '✦' : '◆' }}</span>
    <span class="tier-pips" aria-hidden="true"><i v-for="n in 4" :key="n" :class="{ on: n <= level + 1 }"></i></span>
    <span class="tier-name">{{ label }}</span>
  </span>
</template>
