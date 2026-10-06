<script setup lang="ts">
// The floating friends button (悬浮球): signed-in only, bottom-right above the UID label, with a red badge for
// friend requests waiting for an answer + unread messages. It opens a small panel (a bottom sheet on phones):
// add a friend by UID (the 按 UID 找人 card), answer requests, the friend list, and plain-text chats with friends.
// It hides while a popup or the 参赛 guide is open, and fades out over the register bar, section rail and sky console.
// No Realtime: the panel polls every 5 s while open and visible; the badge rides the header's 60 s poll.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from '../../composables/useI18n'
import { useAuth } from '../../stores/auth'
import { useFlash } from '../../stores/flash'
import { describeError } from '../../lib/errors'
import { parseUid } from '../../lib/friends'
import { blockUser, respondFriendRequest } from '../../lib/friendsApi'
import { boxesOverlap } from '../../lib/uid'
import { DM_MAX_LENGTH, DM_PANEL_POLL_MS, FAB_AVOID, FAB_HIDE, checkBody, fabBadge, mergeMessages, messageSegments, shortTime,
  type DmMessage, type DmOverview, type DmPerson } from '../../lib/dm'
import { loadDmOverview, loadDmThread, reportDm, sendDm } from '../../lib/dmApi'
import { pendingFriendRequests, refreshFriendRequests, unreadDirectMessages } from '../../stores/teamNotifications'
import { openUidCard } from '../../stores/profileCard'
import UserAvatar from '../UserAvatar.vue'
import FriendTeamLine from '../FriendTeamLine.vue'
import { emptyContext } from '../../lib/friendTeams'
import { loadFriendTeamContext } from '../../lib/friendTeamsApi'

const { t, tf } = useI18n()
const i18n = useI18n()
const route = useRoute()
const flash = useFlash()
const { isLoggedIn } = useAuth()
const errorText = (e: unknown) => describeError(e, i18n, ['dm.errors', 'friends.errors'])

const badge = computed(() => fabBadge(pendingFriendRequests.value, unreadDirectMessages.value))
const fabLabel = computed(() => (badge.value ? tf('dm.fab_label_count', { n: badge.value }) : t('dm.fab_label')))

// ---- placement: hide under popups / the guide, fade out over the bars it must not cover ----
const fab = ref<HTMLButtonElement | null>(null)
const hidden = ref(false)
let frame = 0
const shown = (e: Element) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 }
function check() {
  frame = 0
  const el = fab.value
  const modal = Array.from(document.querySelectorAll(FAB_HIDE)).some(shown)
  const box = el?.getBoundingClientRect()
  hidden.value = modal || (!!box && Array.from(document.querySelectorAll(FAB_AVOID)).some(o => boxesOverlap(box, o.getBoundingClientRect(), 6)))
}
function schedule() { if (!frame) frame = requestAnimationFrame(check) }
let observer: MutationObserver | null = null

// ---- panel ----
const panel = ref<HTMLDialogElement | null>(null)
const open = ref(false)
const view = ref<'home' | 'thread'>('home')
const overview = ref<DmOverview>({ requests: 0, incoming: [], people: [] })
const loaded = ref(false)
const loadFailed = ref(false)
const busy = ref(false)
const uidInput = ref('')
const peer = ref<DmPerson | null>(null)
const messages = ref<DmMessage[]>([])
const canSend = ref(true)
const draft = ref('')
const sending = ref(false)
const list = ref<HTMLElement | null>(null)
const composer = ref<HTMLTextAreaElement | null>(null)
// Friends' teams and ranks: loaded once per opening (one RPC + one cached board call), not on every 5 s tick.
const teamCtx = ref(emptyContext())
const draftLength = computed(() => [...draft.value].length)

function syncBadge(o: DmOverview) {
  pendingFriendRequests.value = o.requests
  unreadDirectMessages.value = o.people.reduce((n, p) => n + p.unread, 0)
}
async function loadHome() {
  try { const o = await loadDmOverview(); overview.value = o; syncBadge(o); loadFailed.value = false }
  catch { loadFailed.value = true }
  finally { loaded.value = true }
}
const nearBottom = () => { const el = list.value; return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 80 }
async function toBottom() { await nextTick(); const el = list.value; if (el) el.scrollTop = el.scrollHeight }
async function loadThread(initial = false) {
  const who = peer.value
  if (!who) return
  const after = initial ? 0 : (messages.value.at(-1)?.id ?? 0)
  const stick = initial || nearBottom()
  const th = await loadDmThread(who.user_id, after)
  if (peer.value?.user_id !== who.user_id) return
  canSend.value = th.can_send
  messages.value = initial ? th.messages : mergeMessages(messages.value, th.messages)
  // Opening the thread read it: take its messages off the badge right away.
  unreadDirectMessages.value = Math.max(0, unreadDirectMessages.value - who.unread)
  who.unread = 0
  if (stick && (initial || th.messages.length)) await toBottom()
}

let poll: ReturnType<typeof setInterval> | undefined
function tick() {
  if (!open.value || document.visibilityState !== 'visible') return
  if (view.value === 'home') void loadHome()
  else void loadThread().catch(() => { /* next tick retries */ })
}
async function openPanel() {
  if (open.value) return
  open.value = true
  view.value = 'home'
  await nextTick()
  if (panel.value && !panel.value.open) panel.value.showModal()
  void loadHome()
  void loadFriendTeamContext().then(c => { teamCtx.value = c }).catch(() => {})
  poll = setInterval(tick, DM_PANEL_POLL_MS)
}
function closePanel() { if (panel.value?.open) panel.value.close() }
function onClosed() {
  open.value = false
  if (poll) clearInterval(poll)
  poll = undefined
  peer.value = null
  messages.value = []
  view.value = 'home'
  void refreshFriendRequests()
  // Back to the button once it is visible again (it hides while the panel is open).
  window.setTimeout(() => fab.value?.focus({ preventScroll: true }), 60)
}
async function openThread(p: DmPerson) {
  peer.value = p
  messages.value = []
  canSend.value = p.can_send
  draft.value = ''
  view.value = 'thread'
  try { await loadThread(true) } catch (e) { flash.error(errorText(e)); back(); return }
  await nextTick()
  if (canSend.value) composer.value?.focus({ preventScroll: true })
}
function back() {
  view.value = 'home'
  peer.value = null
  messages.value = []
  void loadHome()
}

function findUid() {
  const uid = parseUid(uidInput.value)
  if (!uid) { flash.error(t('friends.invalid_uid')); return }
  uidInput.value = ''
  openUidCard(uid) // the card opens above the panel; its 加好友 shows here on the next refresh
}
async function answer(id: string, accept: boolean) {
  busy.value = true
  try { await respondFriendRequest(id, accept); if (accept) flash.success(t('friends.result.accepted')); await loadHome() }
  catch (e) { flash.error(errorText(e)) }
  finally { busy.value = false }
}
async function send() {
  const who = peer.value
  if (!who || sending.value) return
  const checked = checkBody(draft.value)
  if ('error' in checked) { flash.error(t(`dm.errors.${checked.error}`)); return }
  sending.value = true
  try { await sendDm(who.user_id, checked.body); draft.value = ''; await loadThread(); await toBottom() }
  catch (e) { flash.error(errorText(e)); if (/not_friends/.test(String((e as Error)?.message))) canSend.value = false }
  finally { sending.value = false; void nextTick(() => composer.value?.focus({ preventScroll: true })) }
}
function onKey(e: KeyboardEvent) {
  // Enter sends on a keyboard; Shift+Enter (and Enter on touch screens) is a new line. Never while composing (IME).
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing || e.keyCode === 229) return
  if (window.matchMedia?.('(pointer: coarse)').matches) return
  e.preventDefault()
  void send()
}
async function report(m: DmMessage) {
  const reason = window.prompt(t('dm.report_prompt'), '')
  if (reason === null) return
  try { await reportDm(m.id, reason); flash.success(t('dm.reported')) } catch (e) { flash.error(errorText(e)) }
}
async function block() {
  const who = peer.value
  if (!who || !window.confirm(t('friends.block_confirm'))) return
  try { await blockUser(who.user_id); flash.success(t('dm.blocked')); back() } catch (e) { flash.error(errorText(e)) }
}

const preview = (p: DmPerson) => (p.last_deleted ? t('dm.deleted') : p.last_body ? (p.last_from_me ? t('dm.you') : '') + p.last_body.replace(/\s+/g, ' ') : '')
const subline = (p: DmPerson) => p.team_name || (p.in_team ? '' : t('friends.no_team'))

onMounted(() => {
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('resize', schedule, { passive: true })
  observer = new MutationObserver(schedule)
  observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['open'] })
  schedule()
})
onBeforeUnmount(() => {
  window.removeEventListener('scroll', schedule)
  window.removeEventListener('resize', schedule)
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
  if (poll) clearInterval(poll)
})
watch(() => route.path, () => { closePanel(); void nextTick(schedule) })
watch(isLoggedIn, logged => { if (!logged) closePanel(); void nextTick(schedule) })
</script>

<template>
  <template v-if="isLoggedIn">
    <button ref="fab" type="button" class="friends-fab" :class="{ 'is-hidden': hidden || open }" data-testid="friends-fab"
      :aria-label="fabLabel" :title="t('dm.fab_label')" aria-haspopup="dialog" :aria-expanded="open" @click="openPanel">
      <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 5.5h16a1.5 1.5 0 0 1 1.5 1.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 3.5v-3.5H4A1.5 1.5 0 0 1 2.5 16V7A1.5 1.5 0 0 1 4 5.5z" />
        <circle cx="8.5" cy="11.5" r=".6" fill="currentColor" /><circle cx="12" cy="11.5" r=".6" fill="currentColor" /><circle cx="15.5" cy="11.5" r=".6" fill="currentColor" />
      </svg>
      <span v-if="badge" class="friends-fab-badge" data-testid="friends-fab-badge" aria-hidden="true">{{ badge }}</span>
    </button>

    <dialog v-if="open" ref="panel" class="friends-fab-panel" aria-labelledby="friends-fab-title" data-testid="friends-fab-panel"
      @click.self="closePanel" @close="onClosed">
      <div class="fp">
        <header class="fp-head">
          <button v-if="view === 'thread'" type="button" class="fp-icon" data-testid="dm-back" :aria-label="t('dm.back')" @click="back">‹</button>
          <template v-if="view === 'thread' && peer">
            <UserAvatar :name="peer.name" :avatar-url="peer.avatar_url" />
            <div class="fp-who">
              <h2 id="friends-fab-title" class="fp-title">{{ peer.name }}</h2>
              <small v-if="subline(peer)" class="fp-sub">{{ subline(peer) }}</small>
            </div>
            <button type="button" class="fp-text-btn" data-testid="dm-block" @click="block">{{ t('friends.block') }}</button>
          </template>
          <template v-else>
            <h2 id="friends-fab-title" class="fp-title">{{ t('dm.title') }}</h2>
            <router-link to="/profile#friends" class="fp-all" data-testid="friends-fab-all" @click="closePanel">{{ t('dm.all_friends') }} →</router-link>
          </template>
          <button type="button" class="fp-icon" data-testid="friends-fab-close" :aria-label="t('dm.close')" @click="closePanel">×</button>
        </header>

        <!-- home: add by UID, requests, friends and chats -->
        <div v-if="view === 'home'" class="fp-body" data-testid="friends-fab-home">
          <form class="fp-add" role="search" @submit.prevent="findUid">
            <label class="sr-only" for="friends-fab-uid">{{ t('dm.add_label') }}</label>
            <input id="friends-fab-uid" v-model="uidInput" data-testid="friends-fab-uid" type="text" inputmode="numeric" autocomplete="off"
              maxlength="20" class="mono" :placeholder="t('dm.add_label')">
            <button class="btn sm" type="submit" :disabled="!uidInput.trim()">{{ t('dm.add_button') }}</button>
          </form>

          <template v-if="overview.incoming.length">
            <h3 class="fp-h">{{ t('friends.incoming_title') }} <span class="fp-count">{{ overview.incoming.length }}</span></h3>
            <ul class="fp-list" data-testid="friends-fab-requests">
              <li v-for="r in overview.incoming" :key="r.id" class="fp-req">
                <UserAvatar :name="r.name" :avatar-url="r.avatar_url" />
                <span class="fp-name">{{ r.name }}</span>
                <span class="fp-req-actions">
                  <button type="button" class="copy-btn" :disabled="busy" data-testid="friends-fab-accept" @click="answer(r.id, true)">{{ t('friends.accept') }}</button>
                  <button type="button" class="copy-btn" :disabled="busy" @click="answer(r.id, false)">{{ t('friends.decline') }}</button>
                </span>
              </li>
            </ul>
          </template>

          <h3 class="fp-h">{{ t('dm.friends') }}</h3>
          <p v-if="loadFailed && !overview.people.length" class="fp-note">{{ t('dm.load_failed') }}</p>
          <p v-else-if="loaded && !overview.people.length" class="fp-note">{{ t('dm.empty') }}</p>
          <ul v-else class="fp-list" data-testid="friends-fab-people">
            <li v-for="p in overview.people" :key="p.user_id">
              <button type="button" class="fp-person" :data-user="p.user_id" @click="openThread(p)">
                <UserAvatar :name="p.name" :avatar-url="p.avatar_url" />
                <span class="fp-person-text">
                  <span class="fp-person-top"><b class="fp-name">{{ p.name }}</b><small v-if="p.last_at" class="fp-time">{{ shortTime(p.last_at) }}</small></span>
                  <small class="fp-preview">{{ preview(p) || subline(p) || ' ' }}</small>
                </span>
                <span v-if="p.unread" class="fp-unread" :aria-label="tf('dm.unread_n', { n: p.unread })">{{ p.unread > 99 ? '99+' : p.unread }}</span>
              </button>
              <FriendTeamLine v-if="p.is_friend" class="fp-team" :user-id="p.user_id" :uid="p.uid" :in-team="p.in_team" :ctx="teamCtx" />
            </li>
          </ul>
          <p class="fp-hint">{{ t('dm.hint') }}</p>
        </div>

        <!-- one conversation -->
        <template v-else-if="peer">
          <div ref="list" class="fp-thread" data-testid="dm-thread" role="log" aria-live="polite" :aria-label="peer.name">
            <p v-if="!messages.length" class="fp-note">{{ t('dm.no_messages') }}</p>
            <div v-for="m in messages" :key="m.id" class="fp-msg" :class="m.from_me ? 'is-mine' : 'is-theirs'" :data-id="m.id">
              <p v-if="m.deleted" class="fp-bubble is-deleted">{{ t('dm.deleted') }}</p>
              <p v-else class="fp-bubble"><template v-for="(s, i) in messageSegments(m.body)" :key="i"><a v-if="s.kind === 'link'" :href="s.href" target="_blank" rel="noopener noreferrer nofollow ugc">{{ s.href }}</a><template v-else>{{ s.text }}</template></template></p>
              <span class="fp-meta">{{ shortTime(m.created_at) }}<button v-if="!m.from_me && !m.deleted" type="button" class="fp-report" data-testid="dm-report" @click="report(m)">{{ t('dm.report') }}</button></span>
            </div>
          </div>
          <form v-if="canSend" class="fp-compose" @submit.prevent="send">
            <label class="sr-only" for="dm-input">{{ t('dm.placeholder') }}</label>
            <textarea id="dm-input" ref="composer" v-model="draft" data-testid="dm-input" rows="2" :maxlength="DM_MAX_LENGTH * 2"
              :placeholder="t('dm.placeholder')" @keydown="onKey" />
            <div class="fp-compose-row">
              <small class="fp-counter" :class="{ 'is-over': draftLength > DM_MAX_LENGTH }">{{ draftLength }}/{{ DM_MAX_LENGTH }}</small>
              <button class="btn sm primary" type="submit" data-testid="dm-send" :disabled="sending || !draft.trim() || draftLength > DM_MAX_LENGTH">{{ sending ? t('dm.sending') : t('dm.send') }}</button>
            </div>
          </form>
          <p v-else class="fp-note fp-cant" data-testid="dm-cant-send">{{ t('dm.cant_send') }}</p>
        </template>
      </div>
    </dialog>
  </template>
</template>

<style scoped>
.friends-fab {
  position: fixed; z-index: 50; right: calc(.65rem + env(safe-area-inset-right, 0px)); bottom: calc(1.85rem + env(safe-area-inset-bottom, 0px));
  display: grid; place-items: center; width: 2.9rem; height: 2.9rem; padding: 0; border-radius: 50% !important; /* the site squares buttons; this one is round */
  border: 1px solid rgba(158, 173, 255, .45); background: rgba(11, 16, 34, .92); color: #dfe5ff; cursor: pointer;
  box-shadow: 0 6px 22px rgba(0, 0, 0, .45); transition: opacity .2s ease, visibility .2s, transform .15s ease, border-color .15s;
}
.friends-fab:hover { border-color: #9eadff; transform: translateY(-1px); }
.friends-fab:focus-visible { outline: 2px solid #9eadff; outline-offset: 3px; }
.friends-fab.is-hidden { opacity: 0; visibility: hidden; pointer-events: none; }
:global(.light) .friends-fab { background: #fff; color: #1d2a55; border-color: rgba(49, 94, 251, .35); box-shadow: 0 6px 20px rgba(20, 30, 60, .18); }
.friends-fab-badge {
  position: absolute; top: -.3rem; right: -.3rem; min-width: 1.2rem; height: 1.2rem; padding: 0 .3rem; border-radius: .6rem;
  background: #e5484d; color: #fff; font: 600 .68rem/1.2rem 'IBM Plex Mono', ui-monospace, monospace; text-align: center;
  box-shadow: 0 0 0 2px #0b1022;
}

.friends-fab-panel {
  position: fixed; inset: auto calc(.65rem + env(safe-area-inset-right, 0px)) calc(1.85rem + env(safe-area-inset-bottom, 0px)) auto;
  width: min(23rem, calc(100vw - 1.3rem)); height: min(36rem, calc(100dvh - 5rem)); max-height: none; margin: 0; padding: 0;
  border: 1px solid rgba(158, 173, 255, .35); background: #0b1022; color: #e8ecf8; box-shadow: 0 24px 80px rgba(0, 0, 0, .6); overflow: hidden;
}
.friends-fab-panel::backdrop { background: rgba(2, 5, 14, .35); }
.fp { display: flex; flex-direction: column; height: 100%; }
.fp-head { display: flex; align-items: center; gap: .55rem; padding: .7rem .7rem .7rem 1rem; border-bottom: 1px solid rgba(255, 255, 255, .1); }
.fp-head :deep(.user-avatar) { width: 2rem; height: 2rem; flex: none; }
.fp-who { min-width: 0; flex: 1; }
.fp-title { margin: 0; flex: 1; font-size: 1rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-who .fp-title { flex: none; }
.fp-sub { display: block; color: rgba(205, 214, 238, .6); font-size: .74rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-all { color: #9eadff; font-size: .8rem; white-space: nowrap; }
.fp-icon { flex: none; width: 2.2rem; height: 2.2rem; border: 0; background: none; color: #aeb6c8; font-size: 1.5rem; line-height: 1; cursor: pointer; }
.fp-icon:hover, .fp-icon:focus-visible { color: #fff; }
.fp-text-btn { flex: none; border: 1px solid #3a3a3a; background: none; color: #aeb6c8; font-size: .72rem; padding: .25rem .55rem; cursor: pointer; }
.fp-text-btn:hover { color: #ffb3b3; border-color: #7a2a2a; }
.fp-body { flex: 1; overflow-y: auto; padding: .8rem 1rem 1rem; overscroll-behavior: contain; }
.fp-add { display: flex; gap: .5rem; }
.fp-add input { flex: 1; min-width: 0; min-height: 36px; padding: .4rem .6rem; border: 1px solid rgba(217, 229, 255, .3); background: rgba(2, 8, 20, .5); color: inherit; }
.fp-h { margin: 1.1rem 0 .3rem; font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: rgba(205, 214, 238, .7); }
.fp-count { display: inline-block; min-width: 1.1rem; padding: 0 .3rem; border-radius: .55rem; background: #e5484d; color: #fff; text-align: center; letter-spacing: 0; }
.fp-list { margin: 0; padding: 0; list-style: none; }
.fp-req { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; padding: .5rem 0; border-bottom: 1px solid rgba(255, 255, 255, .08); }
.fp-req :deep(.user-avatar), .fp-person :deep(.user-avatar) { width: 2.1rem; height: 2.1rem; flex: none; }
.fp-req-actions { display: flex; gap: .4rem; margin-left: auto; }
.fp-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-person { display: flex; width: 100%; align-items: center; gap: .6rem; padding: .55rem .2rem; border: 0; border-bottom: 1px solid rgba(255, 255, 255, .08);
  background: none; color: inherit; text-align: left; cursor: pointer; }
.fp-person:hover, .fp-person:focus-visible { background: rgba(158, 173, 255, .08); }
.fp-team { padding: 0 .2rem .55rem 2.9rem; border-bottom: 1px solid rgba(255, 255, 255, .08); margin-top: -1px; }
.fp-list li:has(.fp-team) .fp-person { border-bottom-color: transparent; }
.fp-person-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
.fp-person-top { display: flex; align-items: baseline; gap: .5rem; min-width: 0; }
.fp-time { margin-left: auto; flex: none; color: rgba(205, 214, 238, .5); font-size: .7rem; }
.fp-preview { color: rgba(205, 214, 238, .62); font-size: .78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-unread { flex: none; min-width: 1.25rem; height: 1.25rem; padding: 0 .35rem; border-radius: .65rem; background: #e5484d; color: #fff; font-size: .7rem; line-height: 1.25rem; text-align: center; }
.fp-note { color: rgba(205, 214, 238, .65); font-size: .85rem; line-height: 1.6; }
.fp-hint { margin-top: 1rem; color: rgba(205, 214, 238, .45); font-size: .72rem; line-height: 1.5; }
.fp-thread { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: .55rem; padding: .9rem 1rem; overscroll-behavior: contain; }
.fp-msg { display: flex; flex-direction: column; max-width: 85%; }
.fp-msg.is-mine { align-self: flex-end; align-items: flex-end; }
.fp-msg.is-theirs { align-self: flex-start; align-items: flex-start; }
.fp-bubble { margin: 0; padding: .45rem .7rem; border-radius: .7rem; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5; font-size: .9rem; }
.is-theirs .fp-bubble { background: rgba(255, 255, 255, .08); border-bottom-left-radius: .2rem; }
.is-mine .fp-bubble { background: #315efb; color: #fff; border-bottom-right-radius: .2rem; }
.fp-bubble a { color: inherit; text-decoration: underline; text-underline-offset: 2px; }
.is-theirs .fp-bubble a { color: #b9c4ff; }
.fp-bubble.is-deleted { background: none; border: 1px dashed rgba(255, 255, 255, .2); color: rgba(205, 214, 238, .55); font-style: italic; }
.fp-meta { display: flex; gap: .5rem; margin-top: .15rem; color: rgba(205, 214, 238, .45); font-size: .66rem; }
.fp-report { border: 0; padding: 0; background: none; color: rgba(205, 214, 238, .45); font-size: .66rem; cursor: pointer; text-decoration: underline; }
.fp-report:hover, .fp-report:focus-visible { color: #ffb3b3; }
.fp-compose { padding: .6rem .8rem .8rem; border-top: 1px solid rgba(255, 255, 255, .1); }
.fp-compose textarea { display: block; width: 100%; min-height: 3.2rem; max-height: 9rem; resize: vertical; padding: .5rem .6rem; border: 1px solid rgba(217, 229, 255, .3);
  background: rgba(2, 8, 20, .5); color: inherit; font: inherit; font-size: .9rem; line-height: 1.45; }
.fp-compose-row { display: flex; align-items: center; justify-content: space-between; margin-top: .45rem; }
.fp-counter { color: rgba(205, 214, 238, .5); font-size: .7rem; }
.fp-counter.is-over { color: #ff8a8a; }
.fp-cant { margin: 0; padding: .8rem 1rem; border-top: 1px solid rgba(255, 255, 255, .1); }

/* Phones: a bottom sheet. */
@media (max-width: 600px) {
  .friends-fab-panel { inset: auto 0 0 0; width: 100%; max-width: none; height: min(85dvh, 40rem); border-width: 1px 0 0; border-radius: .9rem .9rem 0 0;
    padding-bottom: env(safe-area-inset-bottom, 0px); }
  .friends-fab-panel::backdrop { background: rgba(2, 5, 14, .6); }
  .fp-head { padding-top: .9rem; }
  .fp-compose textarea { font-size: 16px; } /* no iOS zoom on focus */
  .fp-add input { font-size: 16px; }
}
@media (prefers-reduced-motion: reduce) { .friends-fab { transition: none; } }
</style>
