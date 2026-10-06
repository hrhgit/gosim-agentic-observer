<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { useRegistrationOpen } from '../../composables/useRegistrationOpen'

const { t } = useI18n()
const route = useRoute()
const { isLoggedIn, state } = useAuth()
const { registrationOpen } = useRegistrationOpen()
// Phones only: from md (768 px) up the header's own register button is always visible.
const mobile = ref(false)
let media: MediaQueryList | null = null
const sync = () => { mobile.value = Boolean(media?.matches) }
onMounted(() => {
  try { media = window.matchMedia('(max-width: 767.98px)'); sync(); media.addEventListener('change', sync) }
  catch { mobile.value = false }
})
onUnmounted(() => media?.removeEventListener('change', sync))
// Wait for the session check so a signed-in participant never sees the bar flash in.
const show = computed(() => mobile.value && state.ready && !isLoggedIn.value && registrationOpen.value && route.path !== '/register')
</script>

<template>
  <template v-if="show">
    <!-- Same height as the bar, at the very end of the page: nothing can scroll underneath it. -->
    <div class="register-bar-spacer" aria-hidden="true"></div>
    <div class="register-bar">
      <router-link to="/register" class="register-float btn primary" data-testid="register-float">
        {{ t('nav.register_float') }} →
      </router-link>
    </div>
  </template>
</template>
