import { createRouter, createWebHistory } from 'vue-router'
import { registrationUrl } from './registration'
import HomePage from './pages/HomePage.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: HomePage },
    { path: '/brief', component: () => import('./pages/VisionPage.vue') },
    { path: '/vision', redirect: '/brief' },
    { path: '/register', component: HomePage, beforeEnter: () => { window.location.replace(registrationUrl); return false } },
  ],
  scrollBehavior(to, _from, savedPosition) {
    if (savedPosition) return savedPosition
    if (to.hash) {
      return new Promise(resolve => {
        window.setTimeout(() => resolve({ el: to.hash, top: 64, behavior: 'smooth' }), 50)
      })
    }
    return { top: 0 }
  },
})

export default router
