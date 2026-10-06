import { supabase } from './supabase'
import { byUidOutcome, normalizeFriends } from './friends'

async function call(name: string, args?: Record<string, unknown>) {
  const { data, error } = await supabase.rpc(name, args)
  if (error) throw error
  return data
}

export const loadFriends = async () => normalizeFriends(await call('my_friends'))
export const sendFriendRequest = async (uid: number) => byUidOutcome(await call('send_friend_request', { p_uid: uid }))
export const inviteToTeamByUid = async (uid: number) => byUidOutcome(await call('send_team_invite_by_uid', { p_uid: uid }))
export const respondFriendRequest = (id: string, accept: boolean) => call('respond_friend_request', { p_request: id, p_accept: accept })
export const cancelFriendRequest = (id: string) => call('cancel_friend_request', { p_request: id })
export const removeFriend = (userId: string) => call('remove_friend', { p_user: userId })
export const blockUser = (userId: string) => call('block_user', { p_user: userId })
export const unblockUser = (userId: string) => call('unblock_user', { p_user: userId })
