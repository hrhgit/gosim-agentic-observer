<script setup lang="ts">
import { computed, ref } from 'vue'
import { githubHandle, teamAvatar } from '../lib/format'

// Uploaded avatar first, then a GitHub photo, then a colored letter disc
// when there is no valid handle/upload or the image 404s.
const props = defineProps<{ name: string; github?: string | null; avatarUrl?: string | null }>()
const uploadFailed = ref(false)
const githubFailed = ref(false)
// Only a valid GitHub username is looked up on github.com; anything else uses the letter disc.
const handle = computed(() => githubHandle(props.github))
</script>

<template>
  <img
    v-if="avatarUrl && !uploadFailed"
    class="user-avatar"
    :src="avatarUrl"
    :alt="name"
    loading="lazy"
    referrerpolicy="no-referrer"
    @error="uploadFailed = true"
  >
  <img
    v-else-if="handle && !githubFailed"
    class="user-avatar"
    :src="`https://github.com/${handle}.png?size=96`"
    :alt="name"
    loading="lazy"
    referrerpolicy="no-referrer"
    @error="githubFailed = true"
  >
  <i v-else class="team-avatar user-avatar" :style="`--team-hue:${teamAvatar(name).hue}`" aria-hidden="true">{{ teamAvatar(name).initial }}</i>
</template>
