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

  return {
    base: normalizeBasePath(env.VITE_BASE_PATH || '/'),
    plugins: [
      vue(),
      tailwindcss(),
      {
        name: 'copy-404',
        closeBundle() {
          try { copyFileSync(resolve('dist/index.html'), resolve('dist/404.html')) } catch {}
        },
      },
    ],
  }
})
