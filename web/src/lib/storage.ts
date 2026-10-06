import { supabase } from './supabase'

export async function sha256Hex(file: Blob): Promise<string> {
  const buffer = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
}

export function randomToken(length = 6): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
  const values = new Uint8Array(length)
  crypto.getRandomValues(values)
  return Array.from(values, v => alphabet[v % alphabet.length]).join('')
}

export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Download an object from a private bucket and hand it to the browser as a file. */
export async function downloadObject(bucket: string, path: string, filename?: string): Promise<void> {
  const { data, error } = await supabase.storage.from(bucket).download(path)
  if (error || !data) throw error ?? new Error('download_failed')
  triggerDownload(data, filename ?? path.split('/').pop() ?? 'download')
}

export async function readObjectText(bucket: string, path: string): Promise<string> {
  const { data, error } = await supabase.storage.from(bucket).download(path)
  if (error || !data) throw error ?? new Error('download_failed')
  return data.text()
}
