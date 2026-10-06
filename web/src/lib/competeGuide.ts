// First-visit guide for the 参赛 workspace (simplified "v2" layout): a short spotlight tour. Pure parts live here
// (steps, copy, which target each step uses, where the tooltip goes) so they are unit-tested; the overlay itself is
// components/competition/CompeteGuide.vue. It counts as a site popup (lib/popupRules): one per page load, seen once
// per person under COMPETE_GUIDE_KEY (bump the version when the tour changes enough to show it again).

export const COMPETE_GUIDE_KEY = 'guide:compete:v1'
export const COMPETE_GUIDE_POPUP = 'compete-guide'

export type GuideTab = 'progress' | 'history' | 'settings'
type Copy = { title: string; body: string }
export type GuideStep = {
  id: string
  /** Workspace tab that must be open before looking for the target (null: no tab needed). */
  tab: GuideTab | null
  /** CSS selectors, best first; the first one on the page is highlighted. */
  targets: string[]
  zh: Copy
  en: Copy
  /** Copy used when only a later (fallback) target is on the page. */
  fallback?: { zh: Copy; en: Copy }
}

const tid = (id: string) => `[data-testid="${id}"]`

export const GUIDE_STEPS: GuideStep[] = [
  {
    id: 'entry', tab: null, targets: [tid('entry-switch')],
    zh: { title: '线上赛 / 练习赛', body: '先选赛程。线上赛的评测计入线上榜。练习赛不影响排名，次数单独算。' },
    en: { title: 'Online or practice', body: 'Pick where you evaluate. Online evaluations count for the online board. Practice does not affect the ranking and has its own daily evaluations.' },
  },
  {
    id: 'upload', tab: 'progress', targets: [tid('project-upload-open'), tid('project-upload-form'), tid('project-versions')],
    zh: { title: '① 上传新版本', body: '上传 ZIP，或填 GitHub 链接（可选分支和子目录）。每次上传就是一个新版本。' },
    en: { title: '① Upload a new version', body: 'Upload a ZIP, or paste a GitHub link (branch and subfolder are optional). Each upload is a new version.' },
  },
  {
    id: 'confirm', tab: 'progress', targets: [tid('project-review-open'), tid('progress-step-2')],
    zh: { title: '② 检查并确认', body: '平台会先准备版本（通常 1–3 分钟）。准备好后，核对设置并点「确认」。确认不占评测次数。' },
    en: { title: '② Review and confirm', body: 'The platform prepares each version first (usually 1–3 minutes). Then review the settings and confirm it. Confirming is free.' },
  },
  {
    id: 'evaluate', tab: 'progress', targets: [tid('project-evaluate-button'), tid('quota-bar'), tid('progress-step-3')],
    zh: { title: '③ 评测', body: '确认后点「评测」。想看稳定性，用「评测 3 次取平均」。每天 40 次，最多同时 4 个。' },
    en: { title: '③ Evaluate', body: 'Once confirmed, press Evaluate. To check stability, use “Evaluate 3 times and average”. 40 evaluations a day, up to 4 at once.' },
  },
  {
    id: 'results', tab: 'progress', targets: [tid('latest-evaluation'), tid('compete-tab-history')],
    zh: { title: '④ 结果与日志', body: '这里看分数。点「查看日志」看运行过程。评测失败时，有按钮直接打开失败日志。' },
    en: { title: '④ Results and logs', body: 'Scores show up here. Press Logs to see what happened. If a run fails, a button opens its log directly.' },
  },
  {
    id: 'settings', tab: 'settings', targets: [`${tid('model-api-settings')} .cw-row-head`, tid('model-api-settings'), tid('compete-tab-settings')],
    zh: { title: '⑤ 设置：密钥与网络', body: '在「添加模型服务」里填模型密钥（如 Kimi）。评测时自动使用，不用开着页面。命令行用的个人令牌也在这里。' },
    en: { title: '⑤ Settings: keys and network', body: 'Add your model key under “Add a model service” (Kimi, for example). Evaluations use it automatically; no page needs to stay open. Personal API tokens for the command line are here too.' },
  },
  {
    id: 'final', tab: 'settings', targets: [tid('final-version'), tid('compete-tab-settings')],
    zh: { title: '⑥ 最终版本', body: '线上赛截止前选好最终版本。不选的话，默认用本队最高分的版本。' },
    en: { title: '⑥ Final version', body: 'Choose your final version before the online deadline. If you don’t, your best-scoring version is used.' },
    fallback: {
      zh: { title: '⑥ 最终版本', body: '练习期间还没有这一项。线上赛开始后，它会出现在「设置」里，记得在截止前选好。' },
      en: { title: '⑥ Final version', body: 'Not shown during practice. Once the online phase starts it appears under Settings; choose it before the deadline.' },
    },
  },
]

export type GuideLocale = 'zh' | 'en' | 'ja' | 'fr'
/** zh gets Chinese; every other locale (en, ja, fr) gets English. */
export const guideLang = (locale: GuideLocale): 'zh' | 'en' => locale === 'zh' ? 'zh' : 'en'

export const GUIDE_UI = {
  zh: { open: '新手指南', prev: '上一步', next: '下一步', done: '完成', skip: '跳过' },
  en: { open: 'Guide', prev: 'Back', next: 'Next', done: 'Done', skip: 'Skip' },
}

export type ResolvedStep = { step: GuideStep; selector: string; fallback: boolean }

/** The target a step highlights, or null when none of its targets is on the page (the step is skipped). */
export function resolveStep(step: GuideStep, exists: (selector: string) => boolean): ResolvedStep | null {
  const i = step.targets.findIndex(exists)
  return i < 0 ? null : { step, selector: step.targets[i]!, fallback: i > 0 }
}

/** Copy for a resolved step: the fallback text only when the step defines one and its main target is missing. */
export function stepCopy(r: ResolvedStep, locale: GuideLocale): Copy {
  const lang = guideLang(locale)
  return r.fallback && r.step.fallback ? r.step.fallback[lang] : r.step[lang]
}

/** Auto-show only on the simplified layout, to someone who has not seen the guide yet, and not over a deep link
 * (a link to #keys etc. wants that place; the guide waits for a later visit). */
export const shouldAutoShow = (layout: string, seen: Set<string>, deepLinked = false) =>
  layout === 'v2' && !deepLinked && !seen.has(COMPETE_GUIDE_KEY)

export type Rect = { top: number; left: number; width: number; height: number }
export type Placement = { top: number; left: number; side: 'below' | 'above' | 'over' }

/**
 * Where the tooltip goes for a highlighted rect (viewport coordinates): below the target when it fits, else above,
 * else pinned to the bottom of the screen over a tall target. Horizontally centred on the target, kept `margin` from
 * the edges (a 390 px phone gets a full-width tooltip).
 */
export function placeTooltip(target: Rect, tip: { width: number; height: number }, view: { width: number; height: number }, gap = 12, margin = 12): Placement {
  const maxLeft = Math.max(margin, view.width - tip.width - margin)
  const left = Math.min(maxLeft, Math.max(margin, target.left + target.width / 2 - tip.width / 2))
  const below = target.top + target.height + gap
  if (below + tip.height <= view.height - margin) return { top: below, left, side: 'below' }
  const above = target.top - gap - tip.height
  if (above >= margin) return { top: above, left, side: 'above' }
  return { top: Math.max(margin, view.height - tip.height - margin), left, side: 'over' }
}

/** The visible part of a target (a tall section is clipped to the screen so the spotlight stays on screen). */
export function clipRect(r: Rect, view: { width: number; height: number }, pad = 6): Rect {
  const top = Math.max(r.top - pad, 0), left = Math.max(r.left - pad, 0)
  const bottom = Math.min(r.top + r.height + pad, view.height), right = Math.min(r.left + r.width + pad, view.width)
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) }
}
