<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
type Item = { title: string; desc: string }
const items = computed(() => t('home.submission.items') as Item[])
</script>

<template>
  <section id="submission" class="poster-section poster-canvas py-20 md:py-28">
    <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="reveal max-w-3xl">
        <span class="poster-kicker">{{ t('home.submission.kicker') }}</span>
        <h2 class="section-title distressed-type mt-8">{{ t('home.submission.title') }}</h2>
      </div>
      <div class="cards cards-3 cards-fit reveal-stagger mt-14">
        <article v-for="(item, index) in items" :key="item.title" class="card card-lift">
          <span class="num">0{{ index + 1 }}</span>
          <h3 class="mt-3">{{ item.title }}</h3>
          <p>{{ item.desc }}</p>
        </article>
      </div>
      <p class="mt-8"><router-link to="/docs" class="label accent">{{ t('home.submission.link') }} →</router-link></p>
    </div>
  </section>
</template>
