<script setup lang="ts">
import { useFlash } from '../../stores/flash'
import { useI18n } from '../../composables/useI18n'
const { items, dismiss } = useFlash()
const { t } = useI18n()
</script>

<template>
  <div class="flash-stack" data-testid="flash" aria-live="polite">
    <div v-for="item in items" :key="item.id" class="flash-item" :class="item.kind" role="status" :data-kind="item.kind">
      <span>{{ item.text }}</span>
      <button type="button" :aria-label="t('common.close')" @click="dismiss(item.id)">×</button>
    </div>
  </div>
</template>
