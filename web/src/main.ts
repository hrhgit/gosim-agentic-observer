import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import { installClickSparks, vTilt, vCountup } from './composables/useFx'
import { consoleGreeting, installMoonFavicon } from './lib/eggs'
import { installFreshnessCheck } from './lib/freshness'
import { markUpdateAvailable } from './lib/updateNotice'
import { initAuth } from './stores/auth'
import './assets/styles/main.css'

// A deploy can replace hashed chunks under a visitor mid-session, so a lazy route's import() can
// 404. Don't reload out from under whatever the visitor is doing (a form, an upload) — just let
// them know a refresh is available, via the UpdateBanner.
window.addEventListener('vite:preloadError', event => {
  event.preventDefault()
  markUpdateAvailable()
})

void initAuth()
createApp(App).directive('tilt', vTilt).directive('countup', vCountup).use(router).mount('#app')
// The app started, so the one-shot retry in index.html for missing asset files has done its job.
try { sessionStorage.removeItem('sac-asset-retry') } catch { /* private mode */ }

installFreshnessCheck()
installClickSparks()
installMoonFavicon()
consoleGreeting()
