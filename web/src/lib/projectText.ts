// Locale-aware wording for the complete-project workflow and the evaluation records.
export type TextLocale = 'en' | 'zh' | 'ja' | 'fr'

const TAGS: Record<TextLocale, string> = { zh: 'zh-CN', en: 'en-US', ja: 'ja-JP', fr: 'fr-FR' }

/** A timestamp in the viewer's time zone, spelled for the page language ("2026/09/26 19:00" on zh). */
export function formatDateTime(value: string | null | undefined, locale: TextLocale, timeZone?: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(TAGS[locale] ?? 'en-US', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    ...(timeZone ? { timeZone } : {}),
  }).format(date)
}

const clock = (hours: number, minutes: number) => `${hours}:${String(minutes).padStart(2, '0')}`

/** The daily evaluation reset with an explicit time zone, e.g. "每天 UTC 0:00（北京时间 8:00）重置". */
export function formatDailyReset(resetsAt: string | null | undefined, locale: TextLocale): string {
  const date = resetsAt ? new Date(resetsAt) : null
  const valid = date && !Number.isNaN(date.getTime())
  const hours = valid ? date.getUTCHours() : 0, minutes = valid ? date.getUTCMinutes() : 0
  const beijing = clock((hours + 8) % 24, minutes)
  return locale === 'zh'
    ? `每天 UTC ${clock(hours, minutes)}（北京时间 ${beijing}）重置`
    : `Resets daily at ${clock(hours, minutes)} UTC (${beijing} Beijing time)`
}

// Fixed revision errors written by the database; project-specific reasons stay as they are.
const REVISION_ERRORS: Record<string, string> = {
  'Project preparation failed. Please retry.': '项目准备失败，请重新提交。',
  'Project preparation did not finish. Please retry.': '项目准备未能完成，请重新提交。',
  'Project scheduling failed. Please retry.': '项目排队失败，请重新提交。',
  'Invalid preparation result. Please retry.': '项目准备结果无效，请重新提交。',
  'Public test scenario is unavailable.': '公开测试场景暂不可用，请稍后重新提交。',
  'Public test failed. Check the project interface and submit again.': '公开场景测试未通过，请检查项目接口后重新上传。',
  'The uploaded file is no longer available. Please re-upload this version; it will not count against your submission quota.':
    '上传的文件已失效，请重新上传该版本，本次不计入次数。',
}
const PREPARATION_FAILED = 'Project preparation failed: '

// Fixed reasons written by the preparation runtime (project_platform/model_client.py
// and model_adapter.py). Unknown or project-specific reasons stay in English.
const ADAPT_HINT_ZH = '可换用更快的模型或接口，或在项目中加入 observer.project.json，这样无需自动适配。'
const CHECK_MODEL_ZH = '请在「参赛」页检查 API 地址、模型名、密钥和余额。'
const PREPARATION_REASONS: Array<[RegExp, (match: RegExpMatchArray) => string]> = [
  [/^The model provider rejected the request(?: \(HTTP (\d{3})\))?\. Check the API endpoint, model name, key and balance on the Participate page\.$/,
    (m) => `模型服务商拒绝了请求${m[1] ? `（HTTP ${m[1]}）` : ''}。${CHECK_MODEL_ZH}`],
  [/^No model API is set up for your team\. Set one under Model API on the Participate page, or add observer\.project\.json so no automatic adaptation is needed\.$/,
    () => '本队还没有设置模型 API。请在「参赛」页的「模型 API」中设置，或在项目中加入 observer.project.json，这样无需自动适配。'],
  [/^No open Participate page answered the model request\./,
    () => '没有打开的「参赛」页面响应模型请求。本队未保存模型密钥，项目准备期间请保持「参赛」页面打开并连接模型 API；也可以在该页面加密保存密钥，或在项目中加入 observer.project.json，这样无需自动适配。'],
  [/^Your model API took longer than the two-minute limit to answer\./, () => '你的模型 API 超过两分钟仍未响应。' + ADAPT_HINT_ZH],
  [/^Your model API did not answer within (\d+) seconds\./, (m) => `你的模型 API 在 ${m[1]} 秒内没有响应。` + ADAPT_HINT_ZH],
  [/^Your model API could not be reached or did not answer in time\./,
    () => '无法连接你的模型 API，或它没有及时响应。请在「参赛」页检查 API 地址。' + ADAPT_HINT_ZH],
  [/^Your model API failed or did not answer in time through the open Participate page\./,
    () => '通过打开的「参赛」页面调用模型 API 失败或超时。' + CHECK_MODEL_ZH + ADAPT_HINT_ZH],
  [/^The adaptation model did not return a valid interface proposal\.$/,
    () => '自动适配用的模型没有返回有效的接口方案。请重新提交，或换用更强的模型，或在项目中加入 observer.project.json。'],
  [/^Model call failed \(HTTP (\d{3})\)\.$/, (m) => `模型调用失败（HTTP ${m[1]}）。请稍后重新提交。`],
  [/^Model service is unavailable\.$/, () => '模型服务暂时不可用，请稍后重新提交。'],
]

function preparationReason(reason: string): string {
  for (const [pattern, zh] of PREPARATION_REASONS) {
    const match = reason.match(pattern)
    if (match) return zh(match)
  }
  return reason
}

/** Revision errors are stored in English; show them in Chinese on the zh page. */
export function revisionErrorText(error: string | null | undefined, locale: TextLocale): string {
  const text = (error ?? '').trim()
  if (!text || locale !== 'zh') return text
  if (REVISION_ERRORS[text]) return REVISION_ERRORS[text]
  if (text.startsWith(PREPARATION_FAILED)) return `项目准备失败：${preparationReason(text.slice(PREPARATION_FAILED.length))}`
  return text
}

/** A failed repository revision can be prepared again from the same URL; a ZIP must be uploaded again. */
export function canPrepareAgain(revision: { status: string; source_kind?: string; source_location?: string; archived_at?: string | null }): boolean {
  return revision.status === 'failed' && !revision.archived_at && revision.source_kind === 'repository' &&
    /^https:\/\/github\.com\/[^/\s]+\/[^/\s]+$/.test(revision.source_location ?? '')
}

/** Ascii-safe, cross-platform folder name for one card's files inside a "download all results" ZIP
 * (e.g. the scenario slug v4-practice-alpha becomes practice-alpha). Falls back to the run id when no
 * scenario slug is known yet, so the combined ZIP never collapses two cards into one folder by accident. */
export function cardFolderName(slug: string | null | undefined, fallback: string): string {
  const folder = String(slug ?? '').replace(/^v4-/, '').replace(/[^a-zA-Z0-9._-]+/g, '-').replace(/^-+|-+$/g, '')
  return folder || fallback
}

/** Folder for the card at `index` (0-based, in card order) of `total`: a numeric prefix keeps
 * α β γ δ / A B C D in card order in every file manager, which sorts folders by name. */
export function orderedCardFolder(index: number, total: number, folder: string): string {
  return `${String(index + 1).padStart(String(total).length, '0')}-${folder}`
}

/** One card's result files for the combined ZIP. A result kept on GitHub arrives wrapped in a
 * single long "<runner repository>-<commit>/" folder; that level is dropped so the files sit
 * directly in the card folder. Contents are untouched; other layouts are returned as they are. */
export function flattenResultEntries<T>(entries: Record<string, T>): Record<string, T> {
  const names = Object.keys(entries).filter(name => !name.endsWith('/'))
  const nested = names.filter(name => name.includes('/'))
  const tops = new Set(nested.map(name => name.slice(0, name.indexOf('/') + 1)))
  // agent.log may sit at the root beside the wrapped files; anything else at the root means no wrapper.
  const wrapped = tops.size === 1 && names.every(name => name.includes('/') || name === 'agent.log')
  const top = wrapped ? [...tops][0]! : ''
  const out: Record<string, T> = {}
  for (const name of names) {
    const flat = top && name.startsWith(top) ? name.slice(top.length) : name
    if (!(flat in out) || name === flat) out[flat] = entries[name]!
  }
  return out
}

/** Execution settings as shown in the review. The platform normalises the declared interface
 * version internally, so that field is left out rather than shown as a different value. */
export function manifestForDisplay(manifest: unknown): unknown {
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) return manifest
  const { protocol: _internal, ...shown } = manifest as Record<string, unknown>
  return shown
}
