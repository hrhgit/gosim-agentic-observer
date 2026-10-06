<script setup lang="ts">
import { computed } from 'vue'
import { parseAnnouncement } from '../../lib/announcementMarkdown'
import AnnouncementInline from './AnnouncementInline.vue'

// Announcement text with a safe markdown subset (bold, inline code, links, lists) and image URLs shown
// as images (no v-html). `poster` sizes images to fit the viewport whole (the popup); otherwise they stay a modest preview.
const props = defineProps<{ text: string | null | undefined; poster?: boolean }>()
const blocks = computed(() => parseAnnouncement(props.text))
</script>

<template>
  <div class="ann-body leading-relaxed" data-testid="announcement-body">
    <template v-for="(b, i) in blocks" :key="i">
      <p v-if="b.kind === 'para'" class="whitespace-pre-line"><AnnouncementInline :inlines="b.inlines" :poster="poster" /></p>
      <p v-else-if="b.kind === 'heading'" class="font-semibold"><AnnouncementInline :inlines="b.inlines" :poster="poster" /></p>
      <ul v-else-if="b.kind === 'ul'" class="ann-list list-disc"><li v-for="(item, j) in b.items" :key="j"><AnnouncementInline :inlines="item" :poster="poster" /></li></ul>
      <ol v-else class="ann-list list-decimal"><li v-for="(item, j) in b.items" :key="j"><AnnouncementInline :inlines="item" :poster="poster" /></li></ol>
    </template>
  </div>
</template>

<style scoped>
.ann-body > * + * { margin-top: .7em; }
.ann-list { padding-left: 1.4em; }
.ann-list li + li { margin-top: .25em; }
</style>
