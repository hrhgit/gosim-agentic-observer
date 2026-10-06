<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'

const { t } = useI18n()
const i18n = useI18n()
const router = useRouter()
const flash = useFlash()
const { state, whenReady } = useAuth()
const form = ref({ password: '', password2: '' })
const errors = ref<string[]>([])
const busy = ref(false)
const ready = ref(false)

onMounted(async () => {
  await whenReady()
  // The recovery session arrives through the URL hash; give the client a moment to consume it.
  if (!state.session) await new Promise(resolve => window.setTimeout(resolve, 800))
  ready.value = true
})

async function submit() {
  errors.value = []
  if (form.value.password.length < 8) errors.value.push(t('auth.errors.password_too_short'))
  if (form.value.password !== form.value.password2) errors.value.push(t('auth.errors.password_mismatch'))
  if (errors.value.length) return
  busy.value = true
  try {
    const { error } = await supabase.auth.updateUser({ password: form.value.password })
    if (error) throw error
    state.recovery = false
    flash.success(t('auth.reset_done'))
    router.replace('/dashboard')
  } catch (e) {
    errors.value = [describeError(e, i18n, ['auth.errors'])]
  } finally { busy.value = false }
}
</script>

<template>
  <main class="poster-canvas min-h-[70vh]">
    <section class="section"><div class="wrap">
      <div class="grid gap-12 lg:grid-cols-[.72fr_1.28fr] lg:gap-20">
        <div>
          <span class="poster-kicker">{{ t('auth.reset_title') }}</span>
          <h1 class="section-title mt-6">{{ t('auth.reset_title') }}</h1>
          <p class="lede mt-6">{{ t('auth.reset_lede') }}</p>
        </div>
        <div class="form-card">
          <p v-if="!ready" class="text3 text-sm">{{ t('common.loading') }}</p>
          <template v-else-if="!state.session">
            <div class="errors"><b>{{ t('auth.reset_invalid_title') }}</b><br>{{ t('auth.reset_no_session') }}</div>
            <router-link class="btn" to="/register?mode=forgot">{{ t('auth.forgot_title') }} →</router-link>
          </template>
          <form v-else @submit.prevent="submit" novalidate>
            <div v-if="errors.length" class="errors" role="alert"><ul class="list-disc pl-5"><li v-for="e in errors" :key="e">{{ e }}</li></ul></div>
            <label class="field"><span>{{ t('auth.password') }}</span><input v-model="form.password" type="password" required minlength="8" autocomplete="new-password"></label>
            <label class="field"><span>{{ t('auth.password2') }}</span><input v-model="form.password2" type="password" required minlength="8" autocomplete="new-password"></label>
            <button class="btn primary" type="submit" :disabled="busy">{{ busy ? t('common.working') : t('auth.reset_submit') }} →</button>
          </form>
        </div>
      </div>
    </div></section>
  </main>
</template>
