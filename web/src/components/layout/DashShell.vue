<script setup lang="ts">
import DashNav from './DashNav.vue'
import AdminNav from './AdminNav.vue'
defineProps<{ kicker: string; title: string; admin?: boolean }>()
</script>

<template>
  <main class="poster-canvas min-h-[70vh]">
    <section class="section tight">
      <div class="wrap">
        <span class="poster-kicker">{{ kicker }}</span>
        <h1 class="section-title sm mt-4 [overflow-wrap:anywhere]">{{ title }}<slot name="title-extra" /></h1>
        <div class="mt-6">
          <AdminNav v-if="admin" />
          <DashNav v-else />
        </div>
        <slot />
      </div>
    </section>
  </main>
</template>
