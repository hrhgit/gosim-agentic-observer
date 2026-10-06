<script setup lang="ts">
import { updateAvailable, reloadForUpdate } from '../../lib/updateNotice'
import { useI18n } from '../../composables/useI18n'

const { t } = useI18n()
</script>

<template>
  <div v-if="updateAvailable" class="banner warning" data-testid="update-banner">
    <div class="wrap banner-in">
      <span class="tag">{{ t('common.refresh') }}</span>
      <span class="min-w-0 flex-1">{{ t('common.update_available') }}</span>
      <button type="button" class="btn sm" @click="reloadForUpdate">{{ t('common.refresh') }}</button>
    </div>
  </div>
</template>
