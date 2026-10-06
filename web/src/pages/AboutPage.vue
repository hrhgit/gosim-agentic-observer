<script setup lang="ts">
import { useI18n } from '../composables/useI18n'
import { useScrollReveal } from '../composables/useScrollReveal'
import PageHead from '../components/layout/PageHead.vue'
import OrganizersBody from '../components/sections/OrganizersBody.vue'

const { t } = useI18n()
useScrollReveal()
</script>

<template>
  <main class="poster-canvas" data-testid="about-page">
    <PageHead :kicker="t('about_page.kicker')" :title="t('home.credibility.title')" />
    <section class="section !pt-0"><div class="wrap">
      <OrganizersBody />
    </div></section>
  </main>
</template>
