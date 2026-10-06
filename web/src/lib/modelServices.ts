// "Add a model service": a provider preset becomes three team variables — the key (encrypted secret),
// the base URL and the model (plain values). Without a prefix it writes the trio the official examples
// read (OPENAI_* or ANTHROPIC_*); with a prefix (KIMI, DEEPSEEK…) several providers live side by side.
// Pure and unit-tested; the component only renders and calls the existing team-variable RPCs.

import providerPresets from './modelProviders.json' with { type: 'json' }

export type ServiceProtocol = 'openai' | 'anthropic'

export type ProviderPreset = {
  id: string
  label: string
  protocol: ServiceProtocol
  baseUrl: string
  /** Empty when the provider's current model name was not verified; the field then shows `placeholder`. */
  model: string
  placeholder: string
  /** Suggested prefix when the default trio is already taken. */
  prefix: string
  /** i18n key of a short provider note, if any. */
  note?: string
  /** The provider's name in `survey26 env model --provider`. */
  cli?: string
}

// Defaults checked against each provider's docs on 2026-10-04 (and the repo's examples for Kimi).
// Shared with both survey26 command-line builds (`survey26 env model --provider <cli>`), so they cannot drift.
export const PROVIDERS: ProviderPreset[] = providerPresets as ProviderPreset[]

export function presetById(id: string): ProviderPreset {
  return PROVIDERS.find(p => p.id === id) ?? PROVIDERS[PROVIDERS.length - 1]
}

/** Normalises a typed prefix: upper case, separators to `_`, no trailing `_`; '' when nothing usable is left. */
export function normalizePrefix(raw: string): string {
  const value = raw.trim().toUpperCase().replace(/[^A-Z0-9_]+/g, '_').replace(/^[^A-Z]+/, '').replace(/_+$/, '')
  return value.slice(0, 40)
}

export type ServiceNames = { key: string; baseUrl: string; model: string }

/** The three variable names: `<prefix>_API_KEY` etc.; no prefix means the protocol's default trio. */
export function serviceNames(protocol: ServiceProtocol, prefix = ''): ServiceNames {
  const p = normalizePrefix(prefix) || (protocol === 'anthropic' ? 'ANTHROPIC' : 'OPENAI')
  return { key: `${p}_API_KEY`, baseUrl: `${p}_BASE_URL`, model: `${p}_MODEL` }
}

type Variable = { name: string; secret: boolean; hint: string; value: string | null; disabled?: boolean }

export type ConfiguredService = {
  prefix: string
  names: ServiceNames
  provider: ProviderPreset | null
  /** Provider label for the summary card: a matching preset, otherwise the URL's host, otherwise the prefix. */
  label: string
  baseUrl: string
  model: string
  hint: string
  hasKey: boolean
  /** Every variable of the service is switched off (kept, not given to runs). */
  disabled: boolean
}

function sameUrl(a: string, b: string): boolean {
  const n = (s: string) => s.trim().toLowerCase().replace(/\/+$/, '')
  return !!a && n(a) === n(b)
}

export function providerForUrl(url: string): ProviderPreset | null {
  return PROVIDERS.find(p => p.baseUrl && sameUrl(p.baseUrl, url)) ??
    (/^https:\/\/api\.kimi\.ai\/coding/i.test(url.trim()) ? presetById('kimi-coding') : null)
}

function hostOf(url: string): string {
  try { return new URL(url).host } catch { return '' }
}

/**
 * Services found among the team's variables: every `<PREFIX>_API_KEY` together with its `<PREFIX>_BASE_URL`
 * and `<PREFIX>_MODEL` when present. Default trios (OPENAI, ANTHROPIC) come first.
 */
export function configuredServices(variables: Variable[]): ConfiguredService[] {
  const byName = new Map(variables.map(v => [v.name, v]))
  const out: ConfiguredService[] = []
  for (const v of variables) {
    const m = /^([A-Z][A-Z0-9_]*)_API_KEY$/.exec(v.name)
    if (!m) continue
    const prefix = m[1]
    const names = { key: v.name, baseUrl: `${prefix}_BASE_URL`, model: `${prefix}_MODEL` }
    const plain = (name: string) => { const x = byName.get(name); return x && !x.secret ? x.value ?? '' : '' }
    const baseUrl = plain(names.baseUrl)
    const model = plain(names.model)
    const provider = providerForUrl(baseUrl) ?? (prefix === 'ANTHROPIC' && !baseUrl ? presetById('anthropic') : null)
    const own = [names.key, names.baseUrl, names.model].map(n => byName.get(n)).filter(x => !!x)
    out.push({ prefix, names, provider, label: provider?.label ?? (hostOf(baseUrl) || prefix), baseUrl, model,
      hint: v.secret ? v.hint : (v.value ?? '').slice(-4), hasKey: true, disabled: own.every(x => x!.disabled === true) })
  }
  const rank = (s: ConfiguredService) => (s.prefix === 'OPENAI' ? 0 : s.prefix === 'ANTHROPIC' ? 1 : 2)
  return out.sort((a, b) => rank(a) - rank(b) || a.prefix.localeCompare(b.prefix))
}

/** Names the quick form manages, so the advanced list can say which ones are covered. */
export function managedNames(services: ConfiguredService[]): Set<string> {
  return new Set(services.flatMap(s => [s.names.key, s.names.baseUrl, s.names.model]))
}

export type ServiceForm = { provider: string; key: string; baseUrl: string; model: string; prefix: string }

export type ServiceWrite =
  /** model: the service's variables are tagged model-related (left out of evaluations without a model). */
  | { op: 'save'; name: string; value: string; secret: boolean; model: true }
  | { op: 'delete'; name: string }

/**
 * The RPC calls for one save. An empty key keeps the stored key (editing); an empty model deletes an old
 * model variable only when it exists. Returns null when the form is incomplete.
 */
export function serviceWrites(form: ServiceForm, existing: Set<string>): ServiceWrite[] | null {
  const preset = presetById(form.provider)
  const names = serviceNames(preset.protocol, form.prefix)
  const baseUrl = form.baseUrl.trim()
  const model = form.model.trim()
  if (!/^https:\/\/\S+$/.test(baseUrl)) return null
  if (!form.key.trim() && !existing.has(names.key)) return null
  const writes: ServiceWrite[] = []
  if (form.key.trim()) writes.push({ op: 'save', name: names.key, value: form.key.trim(), secret: true, model: true })
  writes.push({ op: 'save', name: names.baseUrl, value: baseUrl, secret: false, model: true })
  if (model) writes.push({ op: 'save', name: names.model, value: model, secret: false, model: true })
  else if (existing.has(names.model)) writes.push({ op: 'delete', name: names.model })
  return writes
}

/** How many new variables a save would add (for the per-team limit). */
export function newVariableCount(writes: ServiceWrite[], existing: Set<string>): number {
  return writes.filter(w => w.op === 'save' && !existing.has(w.name)).length
}

/** The service's saved variable names (for switching the whole service off or on). */
export function serviceVariableNames(s: ConfiguredService, existing: Set<string>): string[] {
  return [s.names.key, s.names.baseUrl, s.names.model].filter(n => existing.has(n))
}
