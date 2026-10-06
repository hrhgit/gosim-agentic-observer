<script setup lang="ts">
import { useI18n } from '../../composables/useI18n'
const { t } = useI18n()
</script>

<template>
  <footer class="cosmos-footer border-t py-10 text-white">
    <div class="relative z-10 mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
      <div class="flex flex-col justify-between gap-8 md:flex-row md:items-end">
        <div>
          <div class="text-2xl font-semibold tracking-[-.05em] text-[#f0e9dd]">OPEN <span class="text-[#edb28b]">/</span> OBSERVER</div>
          <div class="mt-3 max-w-xl text-xs leading-relaxed text-white/45">{{ t('footer.copyright') }}</div>
        </div>
        <a href="https://gosim.org" target="_blank" rel="noopener" class="font-mono text-xs uppercase tracking-[.12em] text-white/70 transition-colors hover:text-[#edb28b]">
          {{ t('footer.mainSite') }} ↗
        </a>
      </div>
    </div>
  </footer>
</template>
