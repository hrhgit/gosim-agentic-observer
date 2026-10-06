<script setup lang="ts">
withDefaults(defineProps<{ rows?: number; cols?: number; label?: string }>(), { rows: 5, cols: 4 })
</script>

<template>
  <div class="skeleton" role="status" aria-busy="true" :aria-label="label">
    <div v-for="r in rows" :key="r" class="skeleton-row" :style="{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }">
      <i v-for="c in cols" :key="c" :style="{ width: `${55 + ((r * 7 + c * 13) % 40)}%` }"></i>
    </div>
  </div>
</template>
