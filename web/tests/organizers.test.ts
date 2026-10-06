import assert from 'node:assert/strict'
import test from 'node:test'
import { existsSync, readFileSync } from 'node:fs'
import { ORG_LOGOS, committeePhoto, committeePreview, type CommitteeMember, type OrgItem } from '../src/lib/organizers.ts'

const locales = ['zh', 'en', 'fr', 'ja'].map(l => [l, JSON.parse(readFileSync(new URL(`../src/i18n/${l}.json`, import.meta.url), 'utf8'))] as const)
const media = (path: string) => new URL(`../public/${path}`, import.meta.url)

test('organizer and sponsor have their logo in the repo in every locale', () => {
  for (const [locale, m] of locales) {
    const names = (m.home.credibility.items as OrgItem[]).map(item => item.name)
    assert.deepEqual(names, ['GOSIM Foundation', 'KIMI'], locale)
  }
  for (const { src } of Object.values(ORG_LOGOS)) assert.ok(existsSync(media(src)), src)
})

test('every committee member has a portrait and the same people appear in every locale', () => {
  const photos = (m: any) => (m.home.credibility.committee.members as CommitteeMember[]).map(member => member.photo)
  const zh = photos(locales[0][1])
  assert.equal(zh.length, 7)
  for (const [locale, m] of locales) assert.deepEqual(photos(m), zh, locale)
  for (const member of locales[0][1].home.credibility.committee.members) assert.ok(existsSync(media(committeePhoto(member))), member.photo)
})

test('the credits strip names the first three members and counts the rest', () => {
  const members = locales[0][1].home.credibility.committee.members as CommitteeMember[]
  assert.deepEqual(committeePreview(members), { names: ['翟忠旭', '王伟', '刘德子'], more: 4, total: 7 })
  assert.deepEqual(committeePreview(members.slice(0, 2)), { names: ['翟忠旭', '王伟'], more: 0, total: 2 })
})

test('the credits strip and the about page have copy in every locale', () => {
  for (const [locale, m] of locales) {
    assert.ok(m.nav.about, locale)
    assert.ok(m.meta.pages.about?.title, locale)
    assert.ok(m.about_page?.kicker, locale)
    for (const key of ['aria', 'committee_names', 'name_sep', 'all']) assert.ok(m.home.credits?.[key], `${locale} ${key}`)
  }
})
