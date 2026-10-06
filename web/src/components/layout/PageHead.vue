<script setup lang="ts">
defineProps<{ kicker: string; title: string; lede?: string; note?: string }>()
</script>

<template>
  <div class="wrap page-head">
    <span class="poster-kicker">{{ kicker }}</span>
    <h1 class="mt-6">{{ title }}</h1>
    <p v-if="lede" class="lede">{{ lede }}</p>
    <p v-if="note" class="label mt-4">{{ note }}</p>
    <slot />
  </div>
</template>
