<script setup lang="ts">
// Four steps from sign-up to a first result: done steps are checked and muted, the first
// open step carries the single primary "next" action, later steps wait muted.
import { useI18n } from '../../composables/useI18n'
import { useQuestFlags } from '../../composables/useQuestFlags'
import type { QuestMode, QuestProgress, QuestStepId } from '../../lib/quest'
import SoloTeamButton from '../SoloTeamButton.vue'

const props = defineProps<{ mode: QuestMode; progress: QuestProgress; resultTo: string }>()
const { t, tf } = useI18n()
const { remember } = useQuestFlags()
type StepCopy = { title: string; hint: string; action: string }
const copy = (id: QuestStepId) => t(`dash.quest.${props.mode}.${id}`) as StepCopy
const next = (id: QuestStepId) => tf('dash.quest.next_action', { action: copy(id).action })
</script>

<template>
  <section v-if="progress.finished" class="panel quest-done" data-testid="dash-quest" data-finished="true">
    <span class="quest-star" aria-hidden="true">✦</span>
    <div class="min-w-0">
      <h2>{{ t('dash.quest.done_title') }} · {{ tf('dash.quest.progress', { done: progress.done, total: progress.total }) }}</h2>
      <p class="text2 mt-1 text-sm">{{ t('dash.quest.done_text') }}</p>
    </div>
    <router-link class="btn sm" to="/leaderboard">{{ t('dash.quest.done_board') }} →</router-link>
  </section>
  <section v-else class="panel" data-testid="dash-quest">
    <div class="hd"><h2>{{ t('dash.quest.title') }}</h2><span class="label accent" data-testid="quest-progress">{{ tf('dash.quest.progress', { done: progress.done, total: progress.total }) }}</span></div>
    <div class="quest-bar" role="progressbar" :aria-valuenow="progress.done" aria-valuemin="0" :aria-valuemax="progress.total" :aria-label="t('dash.quest.title')">
      <i :style="{ width: `${(progress.done / progress.total) * 100}%` }"></i>
    </div>
    <ol class="quest-steps">
      <li v-for="(step, index) in progress.steps" :key="step.id" class="quest-step" :class="{ done: step.done, current: step.current }"
        :data-step="step.id" :data-state="step.done ? 'done' : step.current ? 'current' : 'todo'" :aria-current="step.current ? 'step' : undefined">
        <span class="quest-mark" aria-hidden="true">{{ step.done ? '✓' : index + 1 }}</span>
        <div class="min-w-0 flex-1">
          <p class="quest-title">{{ copy(step.id).title }}<span v-if="step.done" class="sr-only"> · {{ t('dash.quest.done_step') }}</span></p>
          <template v-if="step.current">
            <p class="quest-hint">{{ copy(step.id).hint }}</p>
            <div class="actions-inline mt-4">
              <template v-if="step.id === 'team'">
                <router-link class="btn primary sm" to="/team" data-testid="quest-next">{{ next(step.id) }} →</router-link>
                <SoloTeamButton />
              </template>
              <template v-else-if="step.id === 'prepare' && mode === 'practice'">
                <router-link class="btn primary sm" to="/docs" data-testid="quest-next" @click="remember('prepare', mode)">{{ next(step.id) }} →</router-link>
                <router-link class="btn sm" to="/start">{{ t('nav.start') }} →</router-link>
              </template>
              <template v-else-if="step.id === 'prepare'">
                <router-link class="btn primary sm" to="/resources" data-testid="quest-next" @click="remember('prepare', mode)">{{ next(step.id) }} →</router-link>
                <router-link class="btn sm" to="/docs" @click="remember('prepare', mode)">{{ t('dash.quick.docs') }} →</router-link>
              </template>
              <router-link v-else-if="step.id === 'submit'" class="btn primary sm" to="/compete" data-testid="quest-next">{{ next(step.id) }} →</router-link>
              <router-link v-else class="btn primary sm" :to="resultTo" data-testid="quest-next" @click="remember('review', mode)">{{ next(step.id) }} →</router-link>
            </div>
          </template>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.quest-bar { height: 4px; margin: -.25rem 0 1.1rem; background: rgba(255,255,255,.08); }
.quest-bar i { display: block; height: 100%; background: linear-gradient(90deg, #315efb, #7c5cff); transition: width .5s cubic-bezier(.16,1,.3,1); }
.quest-steps { display: grid; gap: .45rem; margin: 0; padding: 0; list-style: none; }
.quest-step { display: flex; align-items: flex-start; gap: .9rem; padding: .75rem .85rem; border: 1px solid transparent; }
.quest-step:not(.current) { opacity: .5; }
.quest-step.current { border-color: rgba(49,94,251,.6); background: rgba(49,94,251,.09); }
.quest-mark {
  display: grid; flex: none; place-items: center; width: 1.75rem; height: 1.75rem; border: 1px solid rgba(255,255,255,.3);
  font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: .75rem; color: #bdbdbd;
}
.quest-step.done .quest-mark { border-color: #1f5a38; color: #59d78d; background: rgba(89,215,141,.08); }
.quest-step.current .quest-mark { border-color: #315efb; color: #fff; background: #315efb; }
.quest-title { padding-top: .2rem; color: #f5f5f5; font-weight: 500; }
.quest-hint { margin-top: .35rem; font-size: .88rem; line-height: 1.6; color: #bdbdbd; }
.quest-done { display: flex; flex-wrap: wrap; align-items: center; gap: .9rem 1.2rem; border-color: rgba(89,215,141,.35); }
.quest-done > .min-w-0 { flex: 1 1 16rem; }
.quest-star { font-size: 1.4rem; color: #ffd27a; text-shadow: 0 0 12px rgba(255,210,122,.6); }
@media (prefers-reduced-motion: reduce) { .quest-bar i { transition: none; } }
</style>
