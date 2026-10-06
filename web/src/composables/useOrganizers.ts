import { computed } from 'vue'
import { useI18n } from './useI18n'
import { appUrl } from './api'
import { ORG_LOGOS, committeePhoto, type CommitteeMember, type OrgItem } from '../lib/organizers'

/** Organizer/sponsor cards and the scientific committee in the current locale (see lib/organizers). */
export function useOrganizers() {
  const { t } = useI18n()
  const items = computed(() => t('home.credibility.items') as OrgItem[])
  const committee = computed(() => t('home.credibility.committee.members') as CommitteeMember[])
  const logo = (name: string): string => (ORG_LOGOS[name] ? appUrl(ORG_LOGOS[name].src) : '')
  const wordmark = (name: string): boolean => !!ORG_LOGOS[name]?.wordmark
  const site = (name: string): string => ORG_LOGOS[name]?.href ?? ''
  const photo = (member: CommitteeMember): string => appUrl(committeePhoto(member))
  return { items, committee, logo, wordmark, site, photo }
}
