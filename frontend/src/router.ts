import { createRouter, createWebHistory } from 'vue-router'

import { setAuthenticationExpiredHandler } from './api'
import { PUBLIC_LOCALE, setLocale } from './i18n'
import { useAuthStore } from './stores/auth'
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/einladung', name: 'register', component: () => import('./views/RegisterView.vue'), meta: { public: true } },
    { path: '/recovery', name: 'recovery', component: () => import('./views/RecoveryView.vue'), meta: { public: true } },
    {
      path: '/einrichtung',
      name: 'setup',
      component: () => import('./views/SetupView.vue'),
      meta: { onboarding: true },
    },
    { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue') },
    { path: '/tage', name: 'daily', component: () => import('./views/DailyView.vue') },
    { path: '/wochen', name: 'weekly', component: () => import('./views/WeeklyView.vue') },
    { path: '/wochentage', name: 'weekdays', component: () => import('./views/WeekdaysView.vue') },
    { path: '/trends', name: 'trends', component: () => import('./views/TrendsView.vue') },
    { path: '/erfolge', name: 'achievements', component: () => import('./views/AchievementsView.vue') },
    { path: '/kalender', name: 'calendar', component: () => import('./views/CalendarView.vue') },
    { path: '/datenqualitaet', name: 'quality', component: () => import('./views/QualityView.vue') },
    { path: '/importe', name: 'imports', component: () => import('./views/ImportsView.vue') },
    {
      path: '/budgets-und-ziele',
      name: 'targets',
      component: () => import('./views/SettingsView.vue'),
      props: { section: 'targets' },
    },
    {
      path: '/konto',
      name: 'account',
      component: () => import('./views/SettingsView.vue'),
      props: { section: 'account' },
    },
    {
      path: '/einstellungen',
      redirect: (to) => ({ name: to.hash === '#zielwerte' ? 'targets' : 'account' }),
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let navigationGeneration = 0
router.beforeEach(async (to) => {
  const generation = ++navigationGeneration
  const auth = useAuthStore()
  if (to.meta.public) {
    auth.beginProfileUpdate()
    setLocale(PUBLIC_LOCALE)
    return true
  }
  if (!(await auth.ensureUser(false))) {
    if (generation !== navigationGeneration) return false
    if (auth.sessionRestoreUnavailable) return false
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (generation !== navigationGeneration) return false
  auth.applyCurrentUserLocale()
  if (auth.needsTargetSetup && to.name !== 'setup') {
    return { name: 'setup' }
  }
  if (!auth.needsTargetSetup && to.name === 'setup') {
    return { name: 'overview' }
  }
  return true
})


setAuthenticationExpiredHandler(() => {
  const auth = useAuthStore()
  auth.clearSession()
  const currentRoute = router.currentRoute.value
  if (currentRoute.meta.public || currentRoute.name === 'login') return
  void router.replace({
    name: 'login',
    query: { next: currentRoute.fullPath },
  })
})

export default router
