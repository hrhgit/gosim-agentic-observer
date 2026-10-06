// Optional WeChat QR code on the profile: the client-side pre-check (the server checks again and also
// verifies the image really is a QR code) and the shapes the RPCs return.
export const QR_MAX_BYTES = 1024 * 1024
export const QR_TYPES = ['image/png', 'image/jpeg', 'image/webp']
export type QrVisibility = 'all' | 'friends'
export interface MyQr { path: string; visibility: QrVisibility; updated_at: string }

/** null when the file may be uploaded, otherwise the error code (i18n key under wechat_qr.errors). */
export function qrFileProblem(file: { size: number; type: string } | null | undefined): string | null {
  if (!file) return 'not_an_image'
  if (!QR_TYPES.includes(file.type)) return 'not_an_image'
  if (file.size > QR_MAX_BYTES) return 'too_large'
  if (file.size === 0) return 'not_an_image'
  return null
}

export function normalizeMyQr(raw: unknown): MyQr | null {
  const r = (raw && typeof raw === 'object' ? raw : null) as Partial<MyQr> | null
  if (!r || typeof r.path !== 'string' || !r.path) return null
  return { path: r.path, visibility: r.visibility === 'all' ? 'all' : 'friends', updated_at: String(r.updated_at ?? '') }
}

/** user id → object path, only for well-formed entries. */
export function normalizeVisibleQrs(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) if (typeof v === 'string' && v) out[k] = v
  }
  return out
}
