import { supabase } from './supabase'
import { normalizeMyQr, normalizeVisibleQrs, type QrVisibility } from './wechatQr'

const BUCKET = 'wechat-qr'
/** Signed URLs live this long; the image is fetched when someone opens it. */
const SIGNED_SECONDS = 120

async function call(name: string, args?: Record<string, unknown>) {
  const { data, error } = await supabase.rpc(name, args)
  if (error) throw error
  return data
}

/** The edge function answers {error: code} on failure; surface the code. */
async function invoke(path: string, init: { method: 'POST' | 'DELETE'; body?: Blob; contentType?: string }) {
  const { data, error } = await supabase.functions.invoke(path, {
    method: init.method, body: init.body, headers: init.contentType ? { 'content-type': init.contentType } : undefined,
  })
  if (error) {
    let code = 'generic'
    try { code = (await (error as { context?: Response }).context?.json())?.error ?? code } catch { /* not JSON */ }
    throw new Error(code)
  }
  return data
}

export const uploadWechatQr = (file: File) => invoke('wechat-qr', { method: 'POST', body: file, contentType: file.type })
export const deleteMyWechatQr = () => invoke('wechat-qr', { method: 'DELETE' })
export const adminDeleteWechatQr = (userId: string) => invoke(`wechat-qr?user=${encodeURIComponent(userId)}`, { method: 'DELETE' })
export const loadMyWechatQr = async () => normalizeMyQr(await call('my_wechat_qr'))
export const setWechatQrVisibility = (v: QrVisibility) => call('set_wechat_qr_visibility', { p_visibility: v })
export const visibleWechatQrs = async (userIds: string[]) =>
  userIds.length ? normalizeVisibleQrs(await call('wechat_qr_visible', { p_users: userIds.slice(0, 200) })) : {}
export const reportWechatQr = (owner: string, reason: string) => call('report_wechat_qr', { p_owner: owner, p_reason: reason })
export const adminWechatQrReports = async () => ((await call('admin_wechat_qr_reports')) ?? []) as Array<{
  owner_id: string; owner_name: string; owner_email: string; path: string; current: boolean; reports: number; reasons: string[] | null; first_at: string }>
export const adminDismissWechatQrReports = (owner: string) => call('admin_dismiss_wechat_qr_reports', { p_owner: owner })

export async function signedWechatQrUrl(path: string): Promise<string> {
  const { data, error } = await supabase.storage.from(BUCKET).createSignedUrl(path, SIGNED_SECONDS)
  if (error || !data?.signedUrl) throw new Error('no_wechat_qr')
  return data.signedUrl
}
