// Assert en.json and zh.json carry exactly the same key set.
import { readFileSync } from 'node:fs'
const flat = (o, p = '') => Object.entries(o).flatMap(([k, v]) => (v && typeof v === 'object' && !Array.isArray(v)) ? flat(v, p ? `${p}.${k}` : k) : [p ? `${p}.${k}` : k])
const en = new Set(flat(JSON.parse(readFileSync(new URL('../src/i18n/en.json', import.meta.url), 'utf8'))))
const zh = new Set(flat(JSON.parse(readFileSync(new URL('../src/i18n/zh.json', import.meta.url), 'utf8'))))
const onlyEn = [...en].filter(k => !zh.has(k)), onlyZh = [...zh].filter(k => !en.has(k))
if (onlyEn.length || onlyZh.length) { console.error('[check-i18n] key mismatch', { onlyEn, onlyZh }); process.exit(1) }
console.log(`[check-i18n] ${en.size} keys, en/zh in sync`)
