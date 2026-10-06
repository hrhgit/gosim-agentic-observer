import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'path'
import { copyFileSync } from 'fs'

function normalizeBasePath(value: string): string {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '/') return '/'
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}/`
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // Stamped once per build, so every HTML file this build emits (index.html, and 404.html copied
  // from it below) carries the same value — the freshness check compares this, not just the
  // hashed entry filename, so it can tell "actually newer" apart from "a CDN edge serving a
  // different generation of the same build" (see web/src/lib/freshness.ts).
  const buildTime = String(Date.now())

  return {
    base: normalizeBasePath(env.VITE_BASE_PATH || '/'),
    plugins: [
      vue(),
      tailwindcss(),
      {
        name: 'stamp-build-time',
        transformIndexHtml(html) {
          return html.replace('<head>', `<head>\n    <meta name="app-build-time" content="${buildTime}" />`)
        },
      },
      {
        name: 'copy-404',
        closeBundle() {
          try { copyFileSync(resolve('dist/index.html'), resolve('dist/404.html')) } catch {}
        },
      },
    ],
  }
})
