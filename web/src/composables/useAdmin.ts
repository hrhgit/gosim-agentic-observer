import { ref } from 'vue'
import { useI18n } from './useI18n'
import { supabase } from '../lib/supabase'
import { describeError } from '../lib/errors'
import { useFlash } from '../stores/flash'

/** Shared helpers for admin pages: busy flag, rpc wrapper, error reporting. */
export function useAdmin() {
  const i18n = useI18n()
  const flash = useFlash()
  const busy = ref(false)

  const report = (e: unknown, namespaces: string[] = []) => flash.error(describeError(e, i18n, namespaces))

  async function rpc<T = unknown>(name: string, args?: Record<string, unknown>): Promise<T> {
    const { data, error } = await supabase.rpc(name, args)
    if (error) throw error
    return data as T
  }

  async function run(action: () => Promise<unknown>, success?: string, namespaces: string[] = []): Promise<boolean> {
    busy.value = true
    try {
      await action()
      if (success) flash.success(success)
      return true
    } catch (e) { report(e, namespaces); return false }
    finally { busy.value = false }
  }

  return { busy, rpc, run, report, flash, t: i18n.t, tf: i18n.tf, pick: i18n.pick }
}
