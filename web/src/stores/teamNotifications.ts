import { ref } from 'vue'
import { supabase } from '../lib/supabase'

/** Unread team notifications (any change since the person last opened the list). */
export const unreadTeamNotifications = ref(0)
/** Requests/invitations waiting for this person's answer; stays until answered, read or not. */
export const pendingTeamActions = ref(0)
/** Friend requests waiting for this person's answer; stays until answered (accept, decline or block). */
export const pendingFriendRequests = ref(0)
/** Unread direct messages from friends (the floating friends button adds them to its badge). */
export const unreadDirectMessages = ref(0)
export async function refreshFriendRequests() {
  // One call for both badges; an older backend without social_counts only has the request count.
  const counts = await supabase.rpc('social_counts')
  if (!counts.error && counts.data) {
    pendingFriendRequests.value = Number(counts.data.requests ?? 0)
    unreadDirectMessages.value = Number(counts.data.unread ?? 0)
    return
  }
  const { data, error } = await supabase.rpc('friend_request_count')
  if (!error) pendingFriendRequests.value = Number(data ?? 0)
}
export async function refreshTeamNotifications() {
  void refreshFriendRequests()
  const { data, error } = await supabase.rpc('team_notification_counts')
  if (!error && data) {
    pendingTeamActions.value = Number(data.pending ?? 0)
    unreadTeamNotifications.value = Number(data.unread ?? 0)
    return
  }
  // Older backend: unread only.
  const old = await supabase.rpc('team_invitation_unread')
  if (!old.error) unreadTeamNotifications.value = Number(old.data ?? 0)
}
export function clearTeamNotifications() { unreadTeamNotifications.value = 0; pendingTeamActions.value = 0; pendingFriendRequests.value = 0; unreadDirectMessages.value = 0 }
export async function teamAction(name: string, args?: Record<string, unknown>) {
  const { data, error } = await supabase.rpc(name, args)
  if (error) throw error
  await refreshTeamNotifications()
  return data
}
