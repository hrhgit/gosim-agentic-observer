<script setup lang="ts">
// 「按 UID 找人」: enter a UID, see that person's card (components/ProfileCardDialog) with 加好友 / 邀请入队.
import { nextTick, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { useAuth } from '../stores/auth'
import { useFlash } from '../stores/flash'
import { parseUid } from '../lib/friends'
import { openUidCard } from '../stores/profileCard'

const { t } = useI18n()
const route = useRoute()
const flash = useFlash()
const { isLoggedIn } = useAuth()
const value = ref('')
const input = ref<HTMLInputElement | null>(null)

function find() {
  const uid = parseUid(value.value)
  if (!uid) { flash.error(t('friends.invalid_uid')); return }
  openUidCard(uid)
}
onMounted(async () => {
  if (route.hash !== '#find-uid') return
  await nextTick()
  input.value?.focus({ preventScroll: true })
})
</script>

<template>
  <div id="find-uid" class="panel find-uid" data-testid="find-uid">
    <div class="hd"><h2>{{ t('friends.find_title') }}</h2></div>
    <template v-if="isLoggedIn">
      <p class="text2 text-sm">{{ t('friends.find_lede') }}</p>
      <form class="find-uid-form mt-4" role="search" @submit.prevent="find">
        <label class="field"><span class="sr-only">UID</span>
          <input ref="input" v-model="value" data-testid="find-uid-input" type="text" inputmode="numeric" autocomplete="off" maxlength="20" class="mono" :placeholder="t('friends.add_placeholder')" :aria-label="t('friends.find_title')">
        </label>
        <button class="btn primary sm" type="submit" data-testid="find-uid-submit" :disabled="!value.trim()">{{ t('friends.find_button') }}</button>
      </form>
    </template>
    <p v-else class="text2 text-sm"><router-link class="accent-l underline underline-offset-2" to="/register?mode=login">{{ t('friends.find_login') }}</router-link></p>
  </div>
</template>

<style scoped>
.find-uid-form { display: flex; flex-wrap: wrap; align-items: stretch; gap: .75rem; }
.find-uid-form .field { flex: 1 1 12rem; margin-bottom: 0; }
.find-uid-form .btn { min-height: 2.9rem; }
</style>
