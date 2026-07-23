import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('./views/LoginView.vue'), meta: { public: true } },
    { path: '/einladung/:token', name: 'register', component: () => import('./views/RegisterView.vue'), meta: { public: true } },
    { path: '/', name: 'overview', component: () => import('./views/OverviewView.vue') },
    { path: '/tage', name: 'daily', component: () => import('./views/DailyView.vue') },
    { path: '/wochen', name: 'weekly', component: () => import('./views/WeeklyView.vue') },
    { path: '/wochentage', name: 'weekdays', component: () => import('./views/WeekdaysView.vue') },
    { path: '/trends', name: 'trends', component: () => import('./views/TrendsView.vue') },
    { path: '/mikronaehrstoffe', name: 'micronutrients', component: () => import('./views/MicronutrientsView.vue') },
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

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  return (await auth.ensureUser()) ? true : { name: 'login', query: { next: to.fullPath } }
})

export default router
