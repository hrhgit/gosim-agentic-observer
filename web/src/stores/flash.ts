import { reactive } from 'vue'

export type FlashKind = 'success' | 'error' | 'info'
export interface FlashItem { id: number; kind: FlashKind; text: string }

const items = reactive<FlashItem[]>([])
let seq = 0

export function flash(kind: FlashKind, text: string, ttl = 7000) {
  const id = ++seq
  items.push({ id, kind, text })
  if (ttl > 0 && typeof window !== 'undefined') window.setTimeout(() => dismiss(id), ttl)
  return id
}
export function dismiss(id: number) {
  const index = items.findIndex(item => item.id === id)
  if (index >= 0) items.splice(index, 1)
}
export function useFlash() {
  return {
    items,
    dismiss,
    success: (text: string) => flash('success', text),
    error: (text: string) => flash('error', text, 10000),
    info: (text: string) => flash('info', text),
  }
}
