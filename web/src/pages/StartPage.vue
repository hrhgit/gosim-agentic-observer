<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'
import PageHead from '../components/layout/PageHead.vue'
import { competition } from '../stores/competition'

/** The newcomer walkthrough: read the docs, build locally, submit. Kept to three
 *  steps so a first-time participant can start in minutes; the stage variant of the
 *  copy comes from the start3 i18n section. */
const { t } = useI18n()
const mode = computed(() => competition.mode === 'competition' ? 'competition' : 'practice')
const copy = computed(() => t(`start3.${mode.value}`))
</script>

<template>
  <main class="poster-canvas page-read">
    <PageHead :kicker="t('nav.start')" :title="t('start_page.title')" :lede="copy.subtitle" />
    <section class="section"><div class="wrap">
      <ol class="start-steps">
        <li class="card" data-testid="start-step-1">
          <h2>{{ copy.s1_title }}</h2>
          <p>{{ copy.s1_desc }}</p>
          <p class="mt-5"><router-link class="btn primary" to="/docs" data-testid="start-docs">{{ copy.s1_cta }} →</router-link></p>
        </li>
        <li class="card" data-testid="start-step-2">
          <h2>{{ copy.s2_title }}</h2>
          <p>{{ copy.s2_lead }}</p>
          <p>{{ copy.s2_desc }}</p>
          <p class="mt-5 start-step2-actions">
            <router-link class="btn" to="/brief">{{ copy.s2_link }} →</router-link>
            <router-link class="btn" to="/resources#examples" data-testid="start-examples">{{ copy.s2_examples_link }} →</router-link>
          </p>
        </li>
        <li class="card" data-testid="start-step-3">
          <h2>{{ copy.s3_title }}</h2>
          <p>{{ copy.s3_intro }}</p>
          <ul class="start-points">
            <li v-for="point in copy.s3_points" :key="point">{{ point }}</li>
          </ul>
          <p class="mt-5"><router-link class="btn primary" to="/compete" data-testid="start-go-compete">{{ copy.s3_cta }} →</router-link></p>
        </li>
      </ol>
      <p class="start-help">{{ copy.help_prefix }}
        <router-link to="/faq">{{ copy.help_faq }}</router-link>
        {{ copy.help_or }}
        <router-link to="/rules">{{ copy.help_rules }}</router-link>{{ copy.help_suffix }}
      </p>
    </div></section>
  </main>
</template>

<style scoped>
.start-steps { display: grid; gap: 1.25rem; margin: 0; padding: 0; list-style: none; }
.start-steps h2 { font-size: 1.25rem; font-weight: 600; letter-spacing: -.02em; color: #f5f5f5; }
.start-cmd {
  display: inline-block; padding: .1rem .45rem; border: 1px solid rgba(158,173,255,.3);
  background: rgba(2,8,20,.6); font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: .85em; color: #9ec1ff; white-space: nowrap;
}
.start-step2-actions { display: flex; flex-wrap: wrap; gap: .6rem; }
.start-points { margin: .75rem 0 0; padding-left: 1.15rem; color: #ccd4e6; font-size: .9rem; line-height: 1.65; }
.start-points li + li { margin-top: .35rem; }
.start-help { margin-top: 2rem; color: #ccd4e6; font-size: .95rem; }
.start-help a { color: #78a6ff; text-decoration: underline; text-underline-offset: 3px; }
.start-help a:hover { color: #9ec1ff; }
@media (min-width: 900px) {
  .start-steps { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
