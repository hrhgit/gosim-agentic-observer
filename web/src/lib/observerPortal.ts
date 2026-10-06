import { StorageApiError } from '@supabase/supabase-js'
import { supabase } from './supabase'
import { withNetworkRetry } from './networkRetry'
import { isAlreadyUploaded, isDuplicateUploadResponse } from './uploadConflict'
import type { EvaluationQuota, FinalVersion } from './projectEvaluation'
import { ZipWithoutCodeError } from './sourceCheck'

export type ProjectRevision = {
  id: string; status: string; source_kind: string; source_location: string; source_digest: string | null
  /** Repository submissions: the branch, tag or commit and the project folder named at submission. */
  source_ref?: string | null; source_subdir?: string | null
  approval_digest: string | null; manifest: Record<string, unknown> | null; adapter_files: Record<string, string>
  explanation: string; error: string; public_test: { passed?: boolean; summary?: string; run_id?: string; status?: string }
  observer_evidence: { notes: string; code_url: string } | null
  created_at: string; approved_at?: string | null; archived_at?: string | null
}
/** The team's model key choice; a saved key is described, never returned. */
export type TeamModel = {
  mode: 'stored' | 'relay'
  protocol: 'openai' | 'anthropic'
  saved: { base_url: string; model: string; key_hint: string; saved_at: string } | null
}
export type TeamVariable = {
  name: string; secret: boolean; hint: string; value: string | null; updated_at: string
  /** Tagged as model-related: left out of evaluations run without a model. Missing on an older database. */
  model?: boolean
  /** Switched off: kept (secrets stay encrypted) but given to no run. */
  disabled?: boolean
}
export type TeamEnvironment = {
  enabled: boolean
  open?: boolean
  variables: TeamVariable[]
  domains: string[]
  relay_key_missing: boolean
  limits: { variables: number; domains: number; value_bytes: number }
  /** 出网线路: labels only; missing until the database provides it. */
  egress_route?: { available: boolean; route: 'direct' | 'cn' | 'overseas'; auto_fallback: boolean } | null
}
export type PortalData = {
  phases: { phase_id: string; projects_enabled: boolean; local_sessions_enabled: boolean; daily_batches: number
    model_token_limit: number; model_call_limit: number; model_concurrency: number; max_active_evaluations?: number; phases: { slug: string; name_en: string; name_zh: string; is_active: boolean
      starts_at: string | null; ends_at: string | null } }[]
  projects: { id: string; title: string; created_at: string; observer_revisions: ProjectRevision[] }[]
  batches: { id: string; mode: string; status: string; score: number | null; created_at: string
    phase_id: string; revision_id: string | null; quota_refunded?: boolean
    /** 本次不提供模型: the evaluation ran without the team's model variables (OBSERVER_MODEL_DISABLED=1). */
    model_disabled?: boolean
    /** Set on the evaluations of one self-check ("evaluate 3 times and average"). */
    repeat_group?: string | null; repeat_runs?: number | null
    observer_runs: { id: string; scenario_id: string; status: string; score: number | null; result_path: string | null
      score_summary?: { raw_score?: { total: number }; calibration?: { version: string } } | null }[] }[]
  providers: { id: string; name: string; base_url: string; models: string[]; shared: boolean; enabled: boolean; daily_token_limit: number }[]
  team_model: TeamModel | null
  model_bases: string[]
  team_environment?: TeamEnvironment | null
  /** Missing until the database provides it; the database enforces the limit either way. */
  quota?: EvaluationQuota[] | null
  /** The team's final version per open formal phase; missing until the database provides it. */
  final_versions?: FinalVersion[] | null
}

/** Thrown by portal(); networkError means the request never reached the server (safe to retry). */
class PortalError extends Error {
  networkError: boolean
  constructor(code: string, networkError: boolean) {
    super(code)
    this.networkError = networkError
  }
}

export async function portal<T>(
  action: string, fields: Record<string, unknown> = {}, onRetry?: (attempt: number) => void,
): Promise<T> {
  return withNetworkRetry(async () => {
    const { data, error } = await supabase.functions.invoke('observer-portal', { body: { action, ...fields } })
    if (error) {
      let code = 'portal_unavailable'
      // A Response means the server actually answered (even with an error); anything
      // else (a dropped connection, a TLS reset) never reached it and is safe to retry.
      const reachedServer = error.context instanceof Response
      let files: string[] | null = null
      if (reachedServer) {
        try {
          const body = await error.context.clone().json()
          code = body.error ?? code
          if (Array.isArray(body.detail?.files)) files = body.detail.files.map(String)
        } catch { /* safe generic error */ }
      }
      if (code === 'zip_has_no_code') throw new ZipWithoutCodeError(files ?? [])
      throw new PortalError(code, !reachedServer)
    }
    if (data?.error) throw new Error(data.error)
    return data.data as T
  }, e => e instanceof PortalError && e.networkError, onRetry)
}

/** A PUT that never reached the server (the dropped-connection case; safe to resend). */
class UploadNetworkError extends Error {
  constructor() { super('upload_failed') }
}

export async function uploadProjectFile(
  file: File, purpose: 'source' | 'csv', onProgress?: (percent: number) => void, onRetry?: (attempt: number) => void,
) {
  const ext = purpose === 'source' ? '.zip' : '.csv'
  if (!file.name.toLowerCase().endsWith(ext)) throw new Error('wrong_file_type')
  if (!file.size || file.size > (purpose === 'source' ? 50 : 20) * 1024 * 1024) throw new Error('file_too_large')
  const slot = await portal<{ id: string; path: string; token: string }>('upload', { purpose }, onRetry)
  const contentType = purpose === 'source' ? 'application/zip' : 'text/csv'
  // With a progress listener the upload goes through XHR: supabase-js fetch uploads
  // never report upload progress. Same endpoint and payload as uploadToSignedUrl.
  if (onProgress) {
    await withNetworkRetry(() => uploadWithProgress(slot.path, slot.token, file, contentType, onProgress), e => e instanceof UploadNetworkError, onRetry)
    return slot.id
  }
  await withNetworkRetry(async () => {
    const { error } = await supabase.storage.from('observer-staging').uploadToSignedUrl(slot.path, slot.token, file, { contentType })
    if (error && !isAlreadyUploaded(error)) throw error
  }, e => !(e instanceof StorageApiError), onRetry)
  return slot.id
}

/** PUT the file to the signed upload URL with progress events (mirrors StorageFileApi.uploadToSignedUrl). */
function uploadWithProgress(path: string, token: string, file: File, contentType: string, onProgress: (percent: number) => void) {
  const api = supabase.storage.from('observer-staging') as unknown as { url: string; headers: Record<string, string> }
  const url = `${api.url}/object/upload/sign/observer-staging/${path}?token=${encodeURIComponent(token)}`
  return new Promise<void>((resolve, reject) => {
    const body = new FormData()
    body.append('cacheControl', '3600')
    // The browser reports .zip as whatever its OS association says (application/zip,
    // application/x-zip-compressed, or empty), and the bucket only allows application/zip;
    // the FormData part's type follows the blob it's given, so re-tag it explicitly.
    body.append('', file.type === contentType ? file : new Blob([file], { type: contentType }), file.name)
    const xhr = new XMLHttpRequest()
    xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100)) }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) return resolve()
      if (isDuplicateUploadResponse(xhr.status, xhr.responseText)) return resolve()
      reject(new Error('upload_failed'))
    }
    xhr.onerror = () => reject(new UploadNetworkError())
    xhr.open('PUT', url)
    for (const [name, value] of Object.entries(api.headers)) xhr.setRequestHeader(name, value)
    xhr.setRequestHeader('x-upsert', 'false')
    xhr.send(body)
  })
}
