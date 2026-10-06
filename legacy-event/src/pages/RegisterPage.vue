<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { User } from '@supabase/supabase-js'
import { useI18n } from '../composables/useI18n'
import { publicSiteUrl } from '../composables/api'
import { isSupabaseConfigured, supabase } from '../lib/supabase'
import controlRoomImage from '../assets/images/cosmos-control-room.jpg'

type AuthMode = 'register' | 'login' | 'forgot' | 'reset'
type NoticeTone = 'success' | 'error' | 'info'

interface ProfileRow {
  id: string
  name: string
  github_id: string | null
  role: string | null
  discord: string | null
  looking_for_team: boolean
  team_id: string | null
  created_at: string
}

interface TeamRow {
  id: string
  name: string
  leader_id: string
  project_idea: string | null
  github_repo: string | null
  max_size: number | null
  created_at: string
}

interface TeamCard extends TeamRow {
  members: ProfileRow[]
}

const { pick } = useI18n()

const authMode = ref<AuthMode>('register')
const authLoading = ref(false)
const dashboardLoading = ref(false)
const teamLoadingId = ref<string | null>(null)
const currentUser = ref<User | null>(null)
const profile = ref<ProfileRow | null>(null)
const profiles = ref<ProfileRow[]>([])
const teams = ref<TeamCard[]>([])
const passwordRecovery = ref(false)
const notice = ref<{ tone: NoticeTone; text: string } | null>(null)

const registration = ref({
  name: '',
  email: '',
  password: '',
  github: '',
  role: '',
  discord: '',
  lookingForTeam: true,
})

const loginForm = ref({ email: '', password: '' })
const resetForm = ref({ password: '', confirmPassword: '' })
const teamForm = ref({ name: '', projectIdea: '', githubRepo: '', maxSize: 4 })

const isLoggedIn = computed(() => Boolean(currentUser.value))
const myTeam = computed(() => teams.value.find(team => team.id === profile.value?.team_id) ?? null)
const totalMembers = computed(() => profiles.value.filter(person => person.team_id).length)
const openTeams = computed(() => teams.value.filter(team => team.max_size === null || team.members.length < team.max_size))

function setNotice(tone: NoticeTone, text: string) {
  notice.value = { tone, text }
}

function clearNotice() {
  notice.value = null
}

function friendlyError(message: string) {
  const translations: Record<string, string> = {
    'Invalid login credentials': '邮箱或密码错误',
    'Email not confirmed': '邮箱尚未确认，请先检查确认邮件',
    'User already registered': '该邮箱已注册，请直接登录',
    'Password should be at least 6 characters.': '密码至少需要 6 个字符',
  }
  return pick(message, translations[message] ?? message)
}

function switchAuthMode(mode: AuthMode) {
  authMode.value = mode
  clearNotice()
}

function normalizeGithub(value: string) {
  return value.trim().replace(/^@/, '')
}

function repositoryUrl(value: string | null) {
  if (!value) return ''
  if (/^https?:\/\//i.test(value)) return value
  return `https://github.com/${value.replace(/^\/+/, '')}`
}

function isTeamFull(team: TeamCard) {
  return team.max_size !== null && team.members.length >= team.max_size
}

function memberLabel(team: TeamCard) {
  const capacity = team.max_size ?? '∞'
  return `${team.members.length} / ${capacity}`
}

async function ensureProfile(user: User) {
  const metadata = user.user_metadata ?? {}
  const { error } = await supabase.from('profiles').insert({
    id: user.id,
    email: user.email,
    name: metadata.name || user.email?.split('@')[0] || '',
    github_id: metadata.github_id || '',
    role: metadata.role || '',
    discord: metadata.discord || '',
    looking_for_team: metadata.looking_for_team ?? true,
  })

  if (error && error.code !== '23505') throw error
}

async function loadDashboard() {
  if (!currentUser.value || !isSupabaseConfigured) return
  dashboardLoading.value = true

  try {
    let [{ data: profileRows, error: profileError }, { data: teamRows, error: teamError }] = await Promise.all([
      supabase
        .from('profiles')
        .select('id, name, github_id, role, discord, looking_for_team, team_id, created_at')
        .order('created_at', { ascending: true }),
      supabase
        .from('teams')
        .select('id, name, leader_id, project_idea, github_repo, max_size, created_at')
        .order('created_at', { ascending: true }),
    ])

    if (profileError) throw profileError
    if (teamError) throw teamError

    if (!(profileRows ?? []).some(row => row.id === currentUser.value?.id)) {
      await ensureProfile(currentUser.value)
      const retry = await supabase
        .from('profiles')
        .select('id, name, github_id, role, discord, looking_for_team, team_id, created_at')
        .order('created_at', { ascending: true })
      if (retry.error) throw retry.error
      profileRows = retry.data
    }

    profiles.value = (profileRows ?? []) as ProfileRow[]
    profile.value = profiles.value.find(row => row.id === currentUser.value?.id) ?? null
    teams.value = ((teamRows ?? []) as TeamRow[]).map(team => ({
      ...team,
      members: profiles.value.filter(person => person.team_id === team.id),
    }))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    dashboardLoading.value = false
  }
}

async function hydrateSession() {
  if (!isSupabaseConfigured) return
  const { data, error } = await supabase.auth.getSession()
  if (error) {
    setNotice('error', friendlyError(error.message))
    return
  }
  currentUser.value = data.session?.user ?? null
  if (currentUser.value) await loadDashboard()
}

async function register() {
  if (!isSupabaseConfigured) return
  clearNotice()
  authLoading.value = true

  try {
    const { data, error } = await supabase.auth.signUp({
      email: registration.value.email.trim(),
      password: registration.value.password,
      options: {
        emailRedirectTo: publicSiteUrl('register'),
        data: {
          name: registration.value.name.trim(),
          github_id: normalizeGithub(registration.value.github),
          role: registration.value.role.trim(),
          discord: registration.value.discord.trim(),
          looking_for_team: registration.value.lookingForTeam,
        },
      },
    })

    if (error) throw error
    if (!data.user) throw new Error(pick('Registration failed.', '报名失败。'))
    if (Array.isArray(data.user.identities) && data.user.identities.length === 0) {
      throw new Error(pick('This email is already registered. Log in instead.', '该邮箱已注册，请直接登录。'))
    }

    if (data.session) {
      currentUser.value = data.user
      await loadDashboard()
      setNotice('success', pick('Registration complete. Your event account is ready.', '报名完成，你的赛事账户已就绪。'))
    } else {
      loginForm.value.email = registration.value.email.trim()
      authMode.value = 'login'
      setNotice('success', pick('Check your inbox to confirm your email, then return here to log in.', '请查收确认邮件，确认后回到此页登录。'))
    }
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    authLoading.value = false
  }
}

async function login() {
  if (!isSupabaseConfigured) return
  clearNotice()
  authLoading.value = true

  try {
    const { data, error } = await supabase.auth.signInWithPassword({
      email: loginForm.value.email.trim(),
      password: loginForm.value.password,
    })
    if (error) throw error
    currentUser.value = data.user
    await loadDashboard()
    setNotice('success', pick('Welcome back. Your registration dashboard is ready.', '欢迎回来，报名面板已就绪。'))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    authLoading.value = false
  }
}

async function sendPasswordReset() {
  if (!isSupabaseConfigured) return
  clearNotice()
  authLoading.value = true

  try {
    const email = loginForm.value.email.trim() || registration.value.email.trim()
    if (!email) throw new Error(pick('Enter your email first.', '请先输入邮箱。'))
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: publicSiteUrl('register'),
    })
    if (error) throw error
    setNotice('success', pick('Password reset instructions are on their way.', '密码重置邮件已发送。'))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    authLoading.value = false
  }
}

async function updatePassword() {
  clearNotice()
  if (resetForm.value.password.length < 6) {
    setNotice('error', pick('Use at least six characters.', '密码至少需要 6 个字符。'))
    return
  }
  if (resetForm.value.password !== resetForm.value.confirmPassword) {
    setNotice('error', pick('The passwords do not match.', '两次输入的密码不一致。'))
    return
  }

  authLoading.value = true
  const { error } = await supabase.auth.updateUser({ password: resetForm.value.password })
  authLoading.value = false
  if (error) {
    setNotice('error', friendlyError(error.message))
    return
  }
  passwordRecovery.value = false
  setNotice('success', pick('Password updated.', '密码已更新。'))
  await hydrateSession()
}

async function logout() {
  await supabase.auth.signOut()
  currentUser.value = null
  profile.value = null
  profiles.value = []
  teams.value = []
  authMode.value = 'login'
  setNotice('info', pick('You are logged out.', '你已退出登录。'))
}

async function createTeam() {
  clearNotice()
  teamLoadingId.value = 'create'
  try {
    const { error } = await supabase.rpc('create_cosmos26_team', {
      p_name: teamForm.value.name.trim(),
      p_project_idea: teamForm.value.projectIdea.trim() || null,
      p_github_repo: teamForm.value.githubRepo.trim() || null,
      p_max_size: teamForm.value.maxSize,
    })
    if (error) throw error
    teamForm.value = { name: '', projectIdea: '', githubRepo: '', maxSize: 4 }
    await loadDashboard()
    setNotice('success', pick('Team created. Your hackathon registration is complete.', '队伍创建成功，你的黑客松报名已完成。'))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    teamLoadingId.value = null
  }
}

async function joinTeam(team: TeamCard) {
  clearNotice()
  teamLoadingId.value = team.id
  try {
    const { error } = await supabase.rpc('join_cosmos26_team', { p_team_id: team.id })
    if (error) throw error
    await loadDashboard()
    setNotice('success', pick(`You joined ${team.name}. Your registration is complete.`, `你已加入 ${team.name}，报名完成。`))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    teamLoadingId.value = null
  }
}

async function leaveTeam() {
  if (!myTeam.value) return
  clearNotice()
  teamLoadingId.value = myTeam.value.id
  try {
    const { error } = await supabase.rpc('leave_cosmos26_team')
    if (error) throw error
    await loadDashboard()
    setNotice('info', pick('You left the team. Join another team to complete registration again.', '你已退出队伍，加入新队伍后即可重新完成报名。'))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    teamLoadingId.value = null
  }
}

async function disbandTeam() {
  if (!myTeam.value) return
  const confirmed = window.confirm(pick(`Disband ${myTeam.value.name}? Every member will be removed from the team.`, `确定解散 ${myTeam.value.name}？所有成员都将离开该队伍。`))
  if (!confirmed) return

  clearNotice()
  teamLoadingId.value = myTeam.value.id
  try {
    const { error } = await supabase.rpc('disband_cosmos26_team', { p_team_id: myTeam.value.id })
    if (error) throw error
    await loadDashboard()
    setNotice('info', pick('Team disbanded.', '队伍已解散。'))
  } catch (error) {
    setNotice('error', friendlyError(error instanceof Error ? error.message : String(error)))
  } finally {
    teamLoadingId.value = null
  }
}

let authSubscription: { unsubscribe: () => void } | null = null

onMounted(async () => {
  const recoveryType = new URLSearchParams(window.location.hash.slice(1)).get('type')
    || new URLSearchParams(window.location.search).get('type')
  if (recoveryType === 'recovery') {
    passwordRecovery.value = true
    authMode.value = 'reset'
  }

  if (!isSupabaseConfigured) return

  const { data } = supabase.auth.onAuthStateChange((event, session) => {
    if (event === 'PASSWORD_RECOVERY') {
      passwordRecovery.value = true
      authMode.value = 'reset'
    }
    if (event === 'SIGNED_OUT') {
      currentUser.value = null
      return
    }
    if ((event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED') && session?.user) {
      currentUser.value = session.user
      window.setTimeout(() => loadDashboard(), 0)
    }
  })
  authSubscription = data.subscription
  // Subscribe before session initialization can consume the recovery URL and
  // emit PASSWORD_RECOVERY, otherwise the reset form may never be displayed.
  await hydrateSession()
})

onUnmounted(() => authSubscription?.unsubscribe())
</script>

<template>
  <main class="poster-canvas min-h-[80vh]">
    <section class="hero-section registration-hero relative overflow-hidden border-b border-white/20 py-20 text-white md:py-28">
      <div class="registration-hero-photo" aria-hidden="true">
        <img :src="controlRoomImage" alt="">
        <div class="registration-plasma plasma-field"></div>
      </div>
      <div class="registration-hero-overlay" aria-hidden="true"></div>
      <div class="relative z-10 mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
        <router-link to="/" class="font-mono text-xs uppercase tracking-[.1em] text-white/55 transition-colors hover:text-white">← {{ pick('Back to hackathon', '返回黑客松主页') }}</router-link>
        <div class="mt-12 grid gap-12 lg:grid-cols-[1.2fr_.8fr] lg:items-end">
          <div>
            <span class="font-mono text-xs uppercase tracking-[.1em] text-[#edb28b]">{{ pick('AGENT OBSERVER / REGISTRATION', '巡天智能体 / 报名') }}</span>
            <h1 class="mt-7 max-w-[14ch] text-balance text-[clamp(3rem,6.5vw,6.75rem)] font-semibold leading-[1.02] tracking-[-.055em]">
              {{ pick('Agent Observer', '巡天智能体') }}
            </h1>
            <p class="mt-8 max-w-2xl text-base leading-[1.7] text-white/70 md:text-lg">
              {{ pick('Create your event account, then create or join a team. A team is the final unit of registration and CosmosBench evaluation.', '创建赛事账户，然后创建或加入队伍。队伍是报名和 CosmosBench 评测的最终单位。') }}
            </p>
          </div>
          <div class="grid grid-cols-3 border-y border-white/30 bg-black/25 py-6 backdrop-blur-sm">
            <div>
              <div class="font-mono text-lg text-accent">01</div>
              <div class="mt-2 text-xs text-white/55">{{ pick('Account', '账户') }}</div>
            </div>
            <div>
              <div class="font-mono text-lg text-accent">02</div>
              <div class="mt-2 text-xs text-white/55">{{ pick('Team', '队伍') }}</div>
            </div>
            <div>
              <div class="font-mono text-lg text-accent">03</div>
              <div class="mt-2 text-xs text-white/55">CosmosBench</div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="registration-body poster-section py-20 md:py-28">
      <div class="mx-auto max-w-[1600px] px-5 md:px-10 xl:px-14">
        <div v-if="!isSupabaseConfigured" class="mb-10 border border-accent bg-bg-card p-5 md:p-6">
          <div class="flex items-start gap-4">
            <span class="mt-1 h-2 w-2 shrink-0 bg-accent"></span>
            <div>
              <h2 class="font-medium text-text-primary">{{ pick('Connect the dedicated Supabase project to open registration', '连接赛事独立的 Supabase 项目后即可开放报名') }}</h2>
              <p class="mt-2 text-sm leading-relaxed text-text-secondary">
                {{ pick('The complete page is ready in preview mode. Add this site\'s own VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY, then apply the event migrations. Factory26 credentials must not be reused.', '完整页面已以预览模式展示。请配置本站独立的 VITE_SUPABASE_URL 与 VITE_SUPABASE_ANON_KEY，并执行赛事迁移脚本。请勿复用 Factory26 凭据。') }}
              </p>
            </div>
          </div>
        </div>

        <div
          v-if="notice"
          class="mb-10 border p-4 text-sm"
          :class="{
            'border-badge-success-text/40 bg-badge-success-bg text-badge-success-text': notice.tone === 'success',
            'border-accent-critical/40 bg-badge-danger-bg text-badge-danger-text': notice.tone === 'error',
            'border-border bg-bg-card text-text-secondary': notice.tone === 'info',
          }"
          role="status"
        >
          {{ notice.text }}
        </div>

        <div v-if="!isLoggedIn || passwordRecovery" class="grid gap-12 lg:grid-cols-[.72fr_1.28fr] lg:gap-20">
          <aside>
            <span class="section-kicker">{{ pick('How it works', '报名流程') }}</span>
            <h2 class="section-title mt-8 text-text-primary">
              {{ pick('One account. One team. One shared benchmark.', '一个账户，一支队伍，同一套基准。') }}
            </h2>
            <div class="mt-10 border-t border-border">
              <div v-for="step in [
                { n: '01', en: 'Create and confirm your account', zh: '创建并确认账户' },
                { n: '02', en: 'Create a team or join an open one', zh: '创建队伍或加入开放队伍' },
                { n: '03', en: 'Submit agent-generated observation results', zh: '提交智能体生成的观测结果' },
              ]" :key="step.n" class="grid grid-cols-[3rem_1fr] border-b border-border py-5">
                <span class="font-mono text-xs text-accent">{{ step.n }}</span>
                <span class="text-sm text-text-secondary">{{ pick(step.en, step.zh) }}</span>
              </div>
            </div>
          </aside>

          <div class="registration-form-panel paper-sheet p-6 md:p-9">
            <template v-if="authMode === 'reset'">
              <span class="mono-label text-accent">{{ pick('Password recovery', '密码恢复') }}</span>
              <h2 class="mt-4 text-2xl font-semibold tracking-[-.04em] text-text-primary md:text-3xl">{{ pick('Choose a new password', '设置新密码') }}</h2>
              <form class="mt-8 grid gap-5" @submit.prevent="updatePassword">
                <label>
                  <span class="field-label">{{ pick('New password', '新密码') }}</span>
                  <input v-model="resetForm.password" class="field" type="password" minlength="6" autocomplete="new-password" required>
                </label>
                <label>
                  <span class="field-label">{{ pick('Confirm password', '确认新密码') }}</span>
                  <input v-model="resetForm.confirmPassword" class="field" type="password" minlength="6" autocomplete="new-password" required>
                </label>
                <button class="primary-action" :disabled="authLoading">{{ authLoading ? pick('Updating…', '正在更新…') : pick('Update password', '更新密码') }}</button>
              </form>
            </template>

            <template v-else>
              <div class="flex border-b border-border" role="tablist">
                <button class="auth-tab" :class="authMode === 'register' ? 'auth-tab-active' : ''" type="button" @click="switchAuthMode('register')">{{ pick('Create account', '创建账户') }}</button>
                <button class="auth-tab" :class="authMode === 'login' || authMode === 'forgot' ? 'auth-tab-active' : ''" type="button" @click="switchAuthMode('login')">{{ pick('Log in', '登录') }}</button>
              </div>

              <form v-if="authMode === 'register'" class="mt-8 grid gap-5 md:grid-cols-2" @submit.prevent="register">
                <label>
                  <span class="field-label">{{ pick('Name', '姓名') }} *</span>
                  <input v-model="registration.name" class="field" required autocomplete="name" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">Email *</span>
                  <input v-model="registration.email" class="field" type="email" required autocomplete="email" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">{{ pick('Password', '密码') }} *</span>
                  <input v-model="registration.password" class="field" type="password" minlength="6" required autocomplete="new-password" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">GitHub</span>
                  <input v-model="registration.github" class="field" placeholder="username" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">{{ pick('Role / background', '角色 / 背景') }}</span>
                  <input v-model="registration.role" class="field" :placeholder="pick('Engineer, astronomer, student…', '工程师、天文学家、学生…')" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">Discord</span>
                  <input v-model="registration.discord" class="field" placeholder="username" :disabled="!isSupabaseConfigured">
                </label>
                <label class="md:col-span-2 flex items-center gap-3 border border-border bg-bg-secondary p-4 text-sm text-text-secondary">
                  <input v-model="registration.lookingForTeam" type="checkbox" class="h-4 w-4 accent-[var(--color-accent)]" :disabled="!isSupabaseConfigured">
                  {{ pick('I am looking for teammates', '我正在寻找队友') }}
                </label>
                <button class="primary-action md:col-span-2" :disabled="!isSupabaseConfigured || authLoading">
                  {{ authLoading ? pick('Creating account…', '正在创建账户…') : pick('Register now', '立即报名') }}
                </button>
              </form>

              <form v-else-if="authMode === 'login'" class="mt-8 grid gap-5" @submit.prevent="login">
                <label>
                  <span class="field-label">Email</span>
                  <input v-model="loginForm.email" class="field" type="email" required autocomplete="email" :disabled="!isSupabaseConfigured">
                </label>
                <label>
                  <span class="field-label">{{ pick('Password', '密码') }}</span>
                  <input v-model="loginForm.password" class="field" type="password" required autocomplete="current-password" :disabled="!isSupabaseConfigured">
                </label>
                <button class="primary-action" :disabled="!isSupabaseConfigured || authLoading">{{ authLoading ? pick('Logging in…', '正在登录…') : pick('Log in', '登录') }}</button>
                <button class="justify-self-start text-xs text-text-tertiary underline underline-offset-4 hover:text-accent" type="button" @click="switchAuthMode('forgot')">{{ pick('Forgot password?', '忘记密码？') }}</button>
              </form>

              <form v-else class="mt-8 grid gap-5" @submit.prevent="sendPasswordReset">
                <p class="text-sm leading-relaxed text-text-secondary">{{ pick('Enter your email and Supabase will send a secure password-reset link.', '输入邮箱，Supabase 将发送安全的密码重置链接。') }}</p>
                <label>
                  <span class="field-label">Email</span>
                  <input v-model="loginForm.email" class="field" type="email" required autocomplete="email" :disabled="!isSupabaseConfigured">
                </label>
                <button class="primary-action" :disabled="!isSupabaseConfigured || authLoading">{{ pick('Send reset link', '发送重置链接') }}</button>
                <button class="justify-self-start text-xs text-text-tertiary underline underline-offset-4 hover:text-accent" type="button" @click="switchAuthMode('login')">← {{ pick('Back to login', '返回登录') }}</button>
              </form>
            </template>
          </div>
        </div>

        <template v-else>
          <div class="flex flex-col justify-between gap-5 border-b border-border pb-8 md:flex-row md:items-end">
            <div>
              <span class="section-kicker">{{ pick('Registration dashboard', '报名面板') }}</span>
              <h2 class="mt-6 text-3xl font-semibold tracking-[-.05em] text-text-primary md:text-4xl">{{ pick('Welcome', '欢迎') }}, {{ profile?.name || currentUser?.email }}</h2>
              <p class="mt-4 text-text-secondary">{{ profile?.team_id ? pick('Your team registration is complete.', '你的队伍报名已完成。') : pick('Create or join a team to complete registration.', '创建或加入队伍以完成报名。') }}</p>
            </div>
            <button class="mono-label text-text-tertiary transition-colors hover:text-accent" @click="logout">{{ pick('Log out', '退出登录') }} →</button>
          </div>

          <div v-if="dashboardLoading" class="py-20 text-center font-mono text-xs uppercase tracking-[.12em] text-text-tertiary">{{ pick('Loading registration…', '正在读取报名信息…') }}</div>

          <div v-else class="mt-10 grid gap-6 md:grid-cols-3">
            <div class="metric-card">
              <span class="metric-value">{{ profiles.length }}</span>
              <span class="metric-label">{{ pick('Registered builders', '已注册开发者') }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-value">{{ teams.length }}</span>
              <span class="metric-label">{{ pick('Teams', '队伍') }}</span>
            </div>
            <div class="metric-card">
              <span class="metric-value">{{ totalMembers }}</span>
              <span class="metric-label">{{ pick('Builders in teams', '已组队开发者') }}</span>
            </div>
          </div>

          <div v-if="myTeam" class="mt-16 border border-accent bg-bg-card p-6 md:p-9">
            <div class="flex flex-col justify-between gap-6 md:flex-row md:items-start">
              <div>
                <span class="mono-label text-accent">{{ pick('Your team / Registration complete', '你的队伍 / 报名完成') }}</span>
                <h3 class="mt-4 text-2xl font-semibold tracking-[-.04em] text-text-primary md:text-4xl">{{ myTeam.name }}</h3>
                <p v-if="myTeam.project_idea" class="mt-5 max-w-3xl leading-relaxed text-text-secondary">{{ myTeam.project_idea }}</p>
              </div>
              <span class="border border-border px-4 py-2 font-mono text-xs text-text-secondary">{{ memberLabel(myTeam) }} {{ pick('members', '人') }}</span>
            </div>

            <div class="mt-8 grid gap-3 border-t border-border pt-6 sm:grid-cols-2 lg:grid-cols-3">
              <div v-for="member in myTeam.members" :key="member.id" class="border border-border bg-bg-secondary p-4">
                <div class="flex items-center justify-between gap-3">
                  <strong class="text-sm text-text-primary">{{ member.name }}</strong>
                  <span v-if="member.id === myTeam.leader_id" class="font-mono text-xs uppercase tracking-[.1em] text-accent">{{ pick('Lead', '队长') }}</span>
                </div>
                <p class="mt-2 text-xs text-text-tertiary">{{ member.role || member.github_id || pick('Hackathon builder', '黑客松参赛者') }}</p>
              </div>
            </div>

            <div class="mt-7 flex flex-wrap gap-3">
              <a v-if="myTeam.github_repo" :href="repositoryUrl(myTeam.github_repo)" target="_blank" rel="noopener" class="secondary-action">GitHub ↗</a>
              <button v-if="currentUser?.id === myTeam.leader_id" class="danger-action" :disabled="teamLoadingId === myTeam.id" @click="disbandTeam">{{ pick('Disband team', '解散队伍') }}</button>
              <button v-else class="secondary-action" :disabled="teamLoadingId === myTeam.id" @click="leaveTeam">{{ pick('Leave team', '退出队伍') }}</button>
            </div>
          </div>

          <div v-else class="mt-16 grid gap-12 lg:grid-cols-[.8fr_1.2fr] lg:gap-20">
            <div>
              <span class="section-kicker">{{ pick('Create a team', '创建队伍') }}</span>
              <h3 class="mt-7 text-2xl font-semibold tracking-[-.04em] text-text-primary md:text-4xl">{{ pick('Start with your observing idea', '从你的观测策略出发') }}</h3>
              <p class="mt-5 leading-relaxed text-text-secondary">{{ pick('You become the team lead. Add a clear idea so other builders know what you want to explore.', '你将成为队长。写下清晰的想法，让其他开发者了解你希望探索的方向。') }}</p>
            </div>
            <form class="grid gap-5 border border-border bg-bg-card p-6 md:grid-cols-2 md:p-9" @submit.prevent="createTeam">
              <label class="md:col-span-2">
                <span class="field-label">{{ pick('Team name', '队伍名称') }} *</span>
                <input v-model="teamForm.name" class="field" required maxlength="80">
              </label>
              <label>
                <span class="field-label">{{ pick('Maximum team size', '队伍人数上限') }}</span>
                <input v-model.number="teamForm.maxSize" class="field" type="number" min="1" max="8" required>
              </label>
              <label>
                <span class="field-label">GitHub repository</span>
                <input v-model="teamForm.githubRepo" class="field" placeholder="org/repository">
              </label>
              <label class="md:col-span-2">
                <span class="field-label">{{ pick('Project idea / observing strategy', '项目想法 / 观测策略') }}</span>
                <textarea v-model="teamForm.projectIdea" class="field min-h-36 resize-y" maxlength="1200"></textarea>
              </label>
              <button class="primary-action md:col-span-2" :disabled="teamLoadingId === 'create'">{{ teamLoadingId === 'create' ? pick('Creating team…', '正在创建队伍…') : pick('Create team and complete registration', '创建队伍并完成报名') }}</button>
            </form>
          </div>

          <div class="mt-20">
            <div class="flex flex-col justify-between gap-5 border-b border-border pb-6 md:flex-row md:items-end">
              <div>
                <span class="section-kicker">{{ pick('Team directory', '队伍名录') }}</span>
                <h3 class="mt-6 text-2xl font-semibold tracking-[-.05em] text-text-primary md:text-4xl">{{ pick('Find your crew', '找到你的队友') }}</h3>
              </div>
              <span class="mono-label text-text-tertiary">{{ openTeams.length }} {{ pick('open teams', '支开放队伍') }}</span>
            </div>

            <div v-if="teams.length" class="mt-8 grid gap-5 md:grid-cols-2">
              <article v-for="team in teams" :key="team.id" class="team-record">
                <div class="flex items-start justify-between gap-5">
                  <div>
                    <h4 class="text-xl font-semibold tracking-[-.03em] text-text-primary">{{ team.name }}</h4>
                    <p class="mt-2 font-mono text-xs uppercase tracking-[.1em] text-text-tertiary">{{ memberLabel(team) }} {{ pick('members', '人') }}</p>
                  </div>
                  <span class="h-2 w-2 shrink-0" :class="isTeamFull(team) ? 'bg-text-muted' : 'bg-accent'"></span>
                </div>
                <p class="mt-5 min-h-12 text-sm leading-relaxed text-text-secondary">{{ team.project_idea || pick('Observing strategy in development.', '观测策略开发中。') }}</p>
                <div class="mt-6 flex items-center justify-between gap-4 border-t border-border pt-5">
                  <a v-if="team.github_repo" :href="repositoryUrl(team.github_repo)" target="_blank" rel="noopener" class="text-xs text-text-tertiary hover:text-accent">GitHub ↗</a>
                  <span v-else class="text-xs text-text-muted">{{ pick('Repository not added', '尚未添加仓库') }}</span>
                  <button
                    v-if="!profile?.team_id"
                    class="small-action"
                    :disabled="isTeamFull(team) || teamLoadingId === team.id"
                    @click="joinTeam(team)"
                  >
                    {{ isTeamFull(team) ? pick('Full', '已满') : teamLoadingId === team.id ? pick('Joining…', '正在加入…') : pick('Join team', '加入队伍') }}
                  </button>
                  <span v-else-if="profile.team_id === team.id" class="font-mono text-xs uppercase tracking-[.1em] text-accent">{{ pick('Your team', '你的队伍') }}</span>
                </div>
              </article>
            </div>
            <div v-else class="mt-8 border border-dashed border-border p-10 text-center text-sm text-text-tertiary">{{ pick('No teams yet. Create the first team.', '还没有队伍，创建第一支队伍吧。') }}</div>
          </div>
        </template>
      </div>
    </section>
  </main>
</template>

<style scoped>
.registration-hero { min-height: 760px; background: #18242f; }

.registration-hero-photo {
  position: absolute;
  inset: 0;
}

.registration-hero-photo > img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 58% center;
  filter: grayscale(1) contrast(1.28) brightness(.58);
}

.registration-hero-overlay {
  position: absolute;
  z-index: 1;
  inset: 0;
  background: linear-gradient(90deg, rgba(24,36,47,.94) 0%, rgba(24,36,47,.75) 48%, rgba(24,36,47,.24) 100%), linear-gradient(0deg, rgba(24,36,47,.85), transparent 60%);
}

.registration-plasma {
  top: 22%;
  right: -5%;
  transform: rotate(-9deg);
}

.registration-body { background: #18242f; }

.registration-form-panel {
  --color-text-primary: #101d29;
  --color-text-secondary: #4c5c68;
  --color-text-tertiary: #8f9ba1;
  --color-text-muted: #8f9ba1;
  --color-border: #8f9ba1;
  --color-bg-secondary: rgba(0,0,0,.035);
  --color-input-bg: rgba(240,233,221,.28);
  --color-input-border: #8f9ba1;
  --color-input-placeholder: #8f9ba1;
  --color-btn-text: #202b36;
  color: #101d29;
}

.field-label {
  display: block;
  margin-bottom: .55rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);
  font-size: .875rem;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.field {
  width: 100%;
  border: 1px solid var(--color-input-border);
  background: var(--color-input-bg);
  padding: .85rem .9rem;
  color: var(--color-text-primary);
  outline: none;
  transition: border-color .18s ease, background-color .18s ease;
}

.field:focus { border-color: var(--color-accent); }
.field:disabled { cursor: not-allowed; opacity: .55; }

.auth-tab {
  flex: 1;
  border-bottom: 2px solid transparent;
  padding: 0 0 1rem;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);
  font-size: .875rem;
  letter-spacing: .1em;
  text-transform: uppercase;
}

.auth-tab-active { border-color: var(--color-accent); color: var(--color-text-primary); }

.primary-action,
.secondary-action,
.danger-action,
.small-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: .875rem;
  font-weight: 600;
  letter-spacing: .1em;
  text-transform: uppercase;
  transition: background-color .18s ease, color .18s ease, border-color .18s ease;
}

.primary-action { min-height: 3.2rem; background: var(--color-btn-bg); padding: .8rem 1.25rem; color: var(--color-btn-text); }
.primary-action:hover:not(:disabled) { background: var(--color-btn-hover); }
.secondary-action { min-height: 2.65rem; border: 1px solid var(--color-border); padding: .65rem 1rem; color: var(--color-text-secondary); }
.secondary-action:hover:not(:disabled) { border-color: var(--color-accent); color: var(--color-accent); }
.danger-action { min-height: 2.65rem; border: 1px solid var(--color-accent-critical); padding: .65rem 1rem; color: var(--color-accent-critical); }
.danger-action:hover:not(:disabled) { background: var(--color-badge-danger-bg); }
.small-action { border: 1px solid var(--color-accent); padding: .55rem .8rem; color: var(--color-accent); }
.small-action:hover:not(:disabled) { background: var(--color-accent); color: var(--color-btn-text); }
.primary-action:disabled,
.secondary-action:disabled,
.danger-action:disabled,
.small-action:disabled { cursor: not-allowed; opacity: .5; }

.metric-card { display: flex; flex-direction: column; border: 1px solid var(--color-border); background: var(--color-bg-card); padding: 1.4rem; }
.metric-value { color: var(--color-text-primary); font-size: 2.25rem; font-weight: 600; letter-spacing: -.05em; }
.metric-label { margin-top: .35rem; color: var(--color-text-tertiary); font-family: var(--font-mono); font-size: .875rem; letter-spacing: .1em; text-transform: uppercase; }
.team-record { border: 1px solid var(--color-border); background: var(--color-bg-card); padding: 1.5rem; transition: border-color .18s ease, background-color .18s ease; }
.team-record:hover { border-color: var(--color-border-strong); background: var(--color-bg-elevated); }

@media (max-width: 720px) {
  .registration-hero { min-height: 680px; }
  .registration-hero-photo > img { object-position: 68% center; }
  .registration-hero-overlay { background: linear-gradient(90deg, rgba(24,36,47,.9), rgba(24,36,47,.52)), linear-gradient(0deg, rgba(24,36,47,.9), transparent 65%); }
  .registration-plasma { top: 17%; right: -42%; }
}
</style>
