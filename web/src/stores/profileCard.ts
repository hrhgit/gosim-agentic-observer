import { reactive } from 'vue'

// The one profile-card dialog (components/ProfileCardDialog.vue): open a person's or a team's card from anywhere.
export const profileCard = reactive({ kind: null as 'person' | 'team' | 'uid' | null, id: '', serial: 0 })
export function openPersonCard(id: string | null | undefined) { if (id) Object.assign(profileCard, { kind: 'person', id, serial: profileCard.serial + 1 }) }
export function openTeamCard(id: string | null | undefined) { if (id) Object.assign(profileCard, { kind: 'team', id, serial: profileCard.serial + 1 }) }
/** 「按 UID 找人」: the card of whoever has this UID (public.find_by_uid; counts towards the daily lookup limit). */
export function openUidCard(uid: number | null) { if (uid) Object.assign(profileCard, { kind: 'uid', id: String(uid), serial: profileCard.serial + 1 }) }
export function closeProfileCard() { Object.assign(profileCard, { kind: null, id: '' }) }
