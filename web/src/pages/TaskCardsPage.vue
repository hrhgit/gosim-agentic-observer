<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from '../composables/useI18n'
import { isSupabaseConfigured } from '../lib/supabase'
import { FORMAL_CARDS, findCard, type CardLanguage, type TaskCard } from '../lib/taskCards'
import { cardPage, downloadCardZip, practiceCards, releasedCardFiles } from '../lib/taskCardSource'
import { competition } from '../stores/competition'
import { useFlash } from '../stores/flash'
import PageHead from '../components/layout/PageHead.vue'
import MarkdownArticle from '../components/content/MarkdownArticle.vue'

const { t, tf, locale } = useI18n()
const flash = useFlash()
const route = useRoute()
const router = useRouter()
const language = computed<CardLanguage>(() => locale.value === 'zh' ? 'zh' : 'en')
const visible = (card: TaskCard | null) => !!card && (card.stage === 'formal' || practiceCards.includes(card))
const card = computed<TaskCard>(() => {
  const requested = findCard(String(route.params.card ?? ''))
  if (visible(requested)) return requested!
  return competition.mode === 'competition' || !practiceCards.length ? FORMAL_CARDS[0]! : practiceCards[0]!
})
const page = ref<string | null>(null)
const files = ref<string[]>([])
const loading = ref(true)
const busy = ref(false)
let request = 0

// An unknown card in the address (a typo, or a card that is not public) falls back to the card list.
watch(() => route.params.card, id => { if (id && !visible(findCard(String(id)))) void router.replace({ path: '/cards', query: route.query }) }, { immediate: true })

watch([card, language], async ([current, lang]) => {
  const mine = ++request
  loading.value = true
  let released: string[] = []
  try { released = isSupabaseConfigured ? await releasedCardFiles(current) : [] } catch { released = [] }
  let text: string | null = null
  try { text = await cardPage(current, lang, released) } catch { text = null }
  if (mine !== request) return
  files.value = released
  page.value = text
  loading.value = false
}, { immediate: true })

async function download() {
  busy.value = true
  try { await downloadCardZip(card.value, files.value) }
  catch { flash.error(t('subs.download_failed')) }
  finally { busy.value = false }
}
</script>

<template>
  <main class="poster-canvas page-read">
    <PageHead :kicker="t('cards_page.kicker')" :title="t('cards_page.title')" :lede="t('cards_page.lede')" />
    <section class="section tight"><div class="wrap">
      <div class="card-sets">
        <nav v-if="practiceCards.length" :aria-label="t('cards_page.practice')">
          <span class="label">{{ t('cards_page.practice') }}</span>
          <div class="tabs">
            <router-link v-for="c in practiceCards" :key="c.id" :to="{ path: `/cards/${c.id}`, query: route.query }" :class="{ active: c.id === card.id }"
              :aria-current="c.id === card.id ? 'page' : undefined" :data-testid="`card-tab-${c.id}`">{{ c.symbol }} {{ c.id }}</router-link>
          </div>
        </nav>
        <nav :aria-label="t('cards_page.formal')">
          <span class="label">{{ t('cards_page.formal') }}</span>
          <div class="tabs">
            <router-link v-for="c in FORMAL_CARDS" :key="c.id" :to="{ path: `/cards/${c.id}`, query: route.query }" :class="{ active: c.id === card.id }"
              :aria-current="c.id === card.id ? 'page' : undefined" :data-testid="`card-tab-${c.id}`">{{ c.symbol }}</router-link>
          </div>
        </nav>
      </div>
    </div></section>
    <section class="section"><div class="wrap" :data-testid="`card-${card.id}`">
      <p v-if="loading" class="text3 text-sm">{{ t('common.loading') }}</p>
      <template v-else-if="page">
        <div class="card-files" data-testid="card-files">
          <template v-if="files.length">
            <button type="button" class="btn sm" :disabled="busy" :data-testid="`card-zip-${card.id}`" @click="download">{{ t('cards_page.download') }} ↓</button>
            <span class="text3 text-xs">{{ tf('cards_page.files', { n: files.length }) }} · {{ t('cards_page.kit_hint') }}</span>
          </template>
          <span v-else class="text3 text-sm">{{ t('cards_page.files_pending') }}</span>
        </div>
        <MarkdownArticle :source="page" />
      </template>
      <div v-else class="card locked" data-testid="card-locked">
        <span class="pill upcoming">{{ t('cards_page.locked_title') }}</span>
        <p class="mt-4">{{ tf('cards_page.locked', { card: card.symbol }) }}</p>
      </div>
    </div></section>
    <section class="section tight"><div class="wrap">
      <div class="hidden-cards-note" data-testid="cards-hidden-cards-note">
        <h3>{{ t('resources.hidden_note_title') }}</h3>
        <p>{{ t('resources.hidden_note_p1') }}</p>
        <p>{{ t('resources.hidden_note_p2') }}</p>
        <p>{{ t('resources.hidden_note_p3') }}</p>
        <p>{{ t('resources.hidden_note_p4') }}</p>
      </div>
    </div></section>
  </main>
</template>

<style scoped>
.card-sets { display: flex; flex-wrap: wrap; gap: 1.5rem 3rem; }
.card-sets .label { display: block; margin-bottom: .35rem; }
/* Greek letters must stay lower case: an upper-case α reads as the hackathon card A. */
.card-sets .tabs a { text-transform: none; letter-spacing: .04em; font-size: .85rem; }
.card-files { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem 1rem; margin-bottom: 2rem; padding-bottom: 1.25rem; border-bottom: 1px solid rgba(255,255,255,.12); }
.locked { max-width: 42rem; }
.hidden-cards-note { max-width: 42rem; border: 1px solid rgba(251,191,36,.3); background: rgba(251,191,36,.06); padding: 1.1rem 1.2rem; }
.hidden-cards-note h3 { font-size: .95rem; color: #fbbf24; margin-bottom: .5rem; }
.hidden-cards-note p { font-size: .875rem; line-height: 1.5; color: rgba(245,247,255,.78); margin-top: .4rem; }
</style>
