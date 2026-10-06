// API_BASE: set to your backend URL if you have one, or empty string for same-origin
export const API_BASE = ''
export const BASE_URL = import.meta.env.BASE_URL

// Resolve an in-app path against the Vite base path.
export function appUrl(path: string = ''): string {
  if (!path) return BASE_URL
  if (/^(?:[a-z]+:)?\/\//i.test(path) || path.startsWith('data:') || path.startsWith('blob:')) return path
  if (path.startsWith('#')) return `${BASE_URL}${path}`
  return `${BASE_URL}${path.replace(/^\/+/, '')}`
}

// Resolve a public absolute URL for auth redirects, email links, and QR codes.
export function publicSiteUrl(path: string = ''): string {
  const configuredSiteUrl = import.meta.env.VITE_SITE_URL?.replace(/\/+$/, '')
  const basePath = BASE_URL === '/' ? '' : BASE_URL.replace(/\/+$/, '')
  const root = configuredSiteUrl || `${window.location.origin}${basePath}`
  if (!path) return root
  if (path.startsWith('#')) return `${root}/${path}`
  return `${root}/${path.replace(/^\/+/, '')}`
}

// Resolve image paths: uploads go to backend, static assets use base path
export function assetUrl(path: string): string {
  if (!path) return ''
  if (/^(?:[a-z]+:)?\/\//i.test(path) || path.startsWith('data:') || path.startsWith('blob:')) return path
  // Static assets (sponsors, icons, default-avatar) — use vite base path
  if (path.startsWith('/')) return appUrl(path)
  return path
}
