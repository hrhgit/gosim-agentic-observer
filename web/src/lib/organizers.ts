/**
 * Organizer, sponsor and scientific committee. The text lives once, in `home.credibility` of the
 * i18n files; the home Organizers section, the hero credits strip and the /about page all read it
 * from there through these helpers.
 */
export type OrgItem = { role: string; name: string; desc: string }
export type CommitteeMember = { photo: string; name: string; title: string; org: string }

/**
 * Logos under web/public/media, keyed by the organisation name used in `home.credibility.items`.
 * A wordmark already spells the name, so the one-line credits strip shows it without the name beside it.
 */
export const ORG_LOGOS: Record<string, { src: string; wordmark: boolean; href: string }> = {
  'GOSIM Foundation': { src: 'media/gosim-logo.svg', wordmark: true, href: 'https://gosim.org/' },
  KIMI: { src: 'media/kimi-logo.png', wordmark: false, href: 'https://www.kimi.com/' },
}

/** Committee portrait under web/public/media. */
export const committeePhoto = (member: CommitteeMember): string => `media/committee/${member.photo}.webp`

/** The first `shown` committee names plus how many are left, for the one-line credits strip. */
export function committeePreview(members: CommitteeMember[], shown = 3): { names: string[]; more: number; total: number } {
  const names = members.slice(0, shown).map(member => member.name)
  return { names, more: members.length - names.length, total: members.length }
}
