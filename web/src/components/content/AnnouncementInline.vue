<script setup lang="ts">
import type { Inline } from '../../lib/announcementMarkdown'

// One run of announcement text (bold, code, links, images), rendered with interpolation only.
defineProps<{ inlines: Inline[]; poster?: boolean }>()
</script>

<template><template v-for="(seg, i) in inlines" :key="i"><template v-if="seg.kind === 'text'">{{ seg.text }}</template><strong v-else-if="seg.kind === 'bold'" class="font-semibold">{{ seg.text }}</strong><code v-else-if="seg.kind === 'code'" class="ann-code">{{ seg.text }}</code><a v-else-if="seg.kind === 'link'" class="accent-l break-all underline underline-offset-2" :href="seg.href" target="_blank" rel="noopener noreferrer">{{ seg.text }}</a><a v-else :href="seg.href" target="_blank" rel="noopener noreferrer" class="mt-4 block" :class="poster ? 'poster' : 'max-w-md'"><img :src="seg.href" alt="" :loading="poster ? 'eager' : 'lazy'" class="w-full rounded border border-border"></a></template></template>

<style scoped>
.ann-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .9em; padding: .05em .35em; border-radius: 4px;
  background: rgba(158,173,255,.14); overflow-wrap: anywhere; }
.poster { max-width: 100%; }
.poster img { width: auto; max-width: 100%; max-height: 72vh; margin: 0 auto; object-fit: contain; }
</style>
