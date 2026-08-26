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
    { path: '/mikronaehrstoffe', name: 'micronutrients', component: () => import('./views/MicronutrientsView.vue') },
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
      path: '/admin',
      component: () => import('./views/AdminLayout.vue'),
      meta: { admin: true },
      children: [
        { path: '', name: 'admin-overview', component: () => import('./views/AdminOverviewView.vue') },
        { path: 'users', name: 'admin-users', component: () => import('./views/AdminUsersView.vue') },
        { path: 'invitations', name: 'admin-invitations', component: () => import('./views/AdminInvitationsView.vue') },
        { path: 'security', name: 'admin-audit', component: () => import('./views/AdminAuditView.vue') },
        { path: 'system', name: 'admin-system', component: () => import('./views/AdminSystemView.vue') },
        { path: 'logs', name: 'admin-logs', component: () => import('./views/AdminLogsView.vue') },
        { path: 'backups', name: 'admin-backups', component: () => import('./views/AdminBackupsView.vue') },
      ],
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
  if (!(await auth.ensureUser(true))) {
    if (generation !== navigationGeneration) return false
    if (auth.sessionRestoreUnavailable) return false
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (generation !== navigationGeneration) return false
  if (to.meta.admin && !auth.user?.is_admin) return { name: 'overview' }
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
