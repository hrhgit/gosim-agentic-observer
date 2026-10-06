<script setup lang="ts">
/** A fixed dot-map of the homepage: one dot per section, the reader always knows
 *  where they are and can jump anywhere — length stops being cognitive load. */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'

const { t } = useI18n()
const props = defineProps<{ sections: { id: string; key: string }[] }>()
const active = ref(props.sections[0]?.id ?? '')
let observer: IntersectionObserver | null = null

onMounted(() => {
  observer = new IntersectionObserver(entries => {
    const visible = entries.filter(entry => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0]
    if (visible) active.value = visible.target.id
  }, { rootMargin: '-35% 0px -45% 0px', threshold: [0, 0.2, 0.5] })
  for (const section of props.sections) {
    const el = document.getElementById(section.id)
    if (el) observer.observe(el)
  }
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <nav class="section-rail" :aria-label="t('home.rail_label')">
    <a v-for="section in sections" :key="section.id" :href="`#${section.id}`" :class="{ active: active === section.id }">
      <span>{{ t(section.key) }}</span>
    </a>
  </nav>
</template>
