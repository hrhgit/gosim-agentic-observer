import { supabase } from './supabase'
import { normalizeOverview, normalizeThread, sendOutcome } from './dm'

async function call(name: string, args?: Record<string, unknown>) {
  const { data, error } = await supabase.rpc(name, args)
  if (error) throw error
  return data
}

export const loadDmOverview = async () => normalizeOverview(await call('dm_overview'))
export const loadDmThread = async (userId: string, after = 0) => normalizeThread(await call('dm_thread', { p_with: userId, p_after: after }))
export const sendDm = async (userId: string, body: string) => sendOutcome(await call('dm_send', { p_to: userId, p_body: body }))
export const reportDm = (messageId: number, reason: string) => call('dm_report', { p_message: messageId, p_reason: reason })
export interface DmReportRow {
  message_id: number; body: string; sent_at: string; sender_id: string; sender_name: string; sender_email: string
  recipient_id: string; recipient_name: string; recipient_email: string; reports: number; reasons: string[] | null; first_at: string
}
export const adminDmReports = async () => ((await call('admin_dm_reports')) ?? []) as DmReportRow[]
export const adminDeleteDm = (messageId: number) => call('admin_delete_dm', { p_message: messageId })
export const adminDismissDmReports = (messageId: number) => call('admin_dismiss_dm_reports', { p_message: messageId })
