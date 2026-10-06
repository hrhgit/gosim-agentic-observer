import assert from 'node:assert/strict'
import test from 'node:test'
import { linkSegments } from '../src/lib/linkify.ts'

test('plain text stays a single text segment', () => {
  assert.deepEqual(linkSegments('no links here'), [{ kind: 'text', text: 'no links here' }])
  assert.deepEqual(linkSegments(''), [])
  assert.deepEqual(linkSegments(null), [])
})

test('URLs become links and trailing punctuation stays text', () => {
  assert.deepEqual(linkSegments('会议：https://meeting.tencent.com/dm/Ts4EejVY2FMd。欢迎'), [
    { kind: 'text', text: '会议：' },
    { kind: 'link', href: 'https://meeting.tencent.com/dm/Ts4EejVY2FMd' },
    { kind: 'text', text: '。欢迎' },
  ])
  assert.deepEqual(linkSegments('see https://example.org/a.'), [
    { kind: 'text', text: 'see ' },
    { kind: 'link', href: 'https://example.org/a' },
    { kind: 'text', text: '.' },
  ])
})

test('image URLs become image segments', () => {
  assert.deepEqual(linkSegments('海报\nhttps://create.gosim.org/survey26/media/talks/talk.jpg'), [
    { kind: 'text', text: '海报\n' },
    { kind: 'image', href: 'https://create.gosim.org/survey26/media/talks/talk.jpg' },
  ])
})

test('non-http schemes are left as text', () => {
  assert.deepEqual(linkSegments('javascript:alert(1)'), [{ kind: 'text', text: 'javascript:alert(1)' }])
})
