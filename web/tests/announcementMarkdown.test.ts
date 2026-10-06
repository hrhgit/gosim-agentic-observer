import assert from 'node:assert/strict'
import test from 'node:test'
import { parseAnnouncement, parseInline } from '../src/lib/announcementMarkdown.ts'

test('bold, inline code and links become segments; unsafe links stay text', () => {
  assert.deepEqual(parseInline('请设置 **OPENAI_MODEL** 为 `kimi-for-coding`，见 [文档](https://x.org/d)'), [
    { kind: 'text', text: '请设置 ' }, { kind: 'bold', text: 'OPENAI_MODEL' }, { kind: 'text', text: ' 为 ' },
    { kind: 'code', text: 'kimi-for-coding' }, { kind: 'text', text: '，见 ' }, { kind: 'link', text: '文档', href: 'https://x.org/d' },
  ])
  assert.deepEqual(parseInline('[x](javascript:alert(1)) <b>hi</b>'), [{ kind: 'text', text: '[x](javascript:alert(1)) <b>hi</b>' }])
  assert.deepEqual(parseInline('2 ** 3'), [{ kind: 'text', text: '2 ** 3' }])
})

test('bare URLs and images still work', () => {
  assert.deepEqual(parseInline('see https://a.org/x. and https://a.org/p.png'), [
    { kind: 'text', text: 'see ' }, { kind: 'link', href: 'https://a.org/x', text: 'https://a.org/x' }, { kind: 'text', text: '. and ' },
    { kind: 'image', href: 'https://a.org/p.png' },
  ])
})

test('paragraphs, headings and lists', () => {
  const blocks = parseAnnouncement('## 更新\n第一行\n第二行\n\n- a **b**\n- c\n1. one\n2) two\n\n结尾')
  assert.deepEqual(blocks.map(b => b.kind), ['heading', 'para', 'ul', 'ol', 'para'])
  assert.deepEqual(blocks[1], { kind: 'para', inlines: [{ kind: 'text', text: '第一行\n第二行' }] })
  assert.equal(blocks[2].kind === 'ul' && blocks[2].items.length, 2)
  assert.deepEqual(parseAnnouncement(''), [])
  assert.deepEqual(parseAnnouncement(null), [])
})
