import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import packageMetadata from '../package.json'
import App from '../src/App.vue'
import { useAuthStore } from '../src/stores/auth'

const user = {
  id: 'user-1',
  username: 'friend',
  language: 'de',
  timezone: 'Europe/Berlin',
  week_starts_on: 0,
  raw_payload_retention_days: 0,
  is_admin: false,
  is_active: true,
  deactivated_at: null,
}

async function mountApp(path = '/tage', isAdmin = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.user = { ...user, is_admin: isAdmin }
  auth.needsTargetSetup = false

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<h1>Übersicht</h1>' } },
      { path: '/tage', name: 'daily', component: { template: '<h1>Tagesverlauf</h1>' } },
      { path: '/wochen', name: 'weekly', component: { template: '<h1>Wochenbudget</h1>' } },
      { path: '/wochentage', name: 'weekdays', component: { template: '<h1>Wochentage</h1>' } },
      { path: '/kalender', name: 'calendar', component: { template: '<h1>Kalender</h1>' } },
      { path: '/trends', name: 'trends', component: { template: '<h1>Trends</h1>' } },
      { path: '/mikronaehrstoffe', name: 'micronutrients', component: { template: '<h1>Mikronährstoffe</h1>' } },
      { path: '/datenqualitaet', name: 'quality', component: { template: '<h1>Datenstatus</h1>' } },
      { path: '/importe', name: 'imports', component: { template: '<h1>Importe</h1>' } },
      { path: '/erfolge', name: 'achievements', component: { template: '<h1>Erfolge</h1>' } },
      { path: '/budgets-und-ziele', name: 'targets', component: { template: '<h1>Ziele</h1>' } },
      { path: '/konto', name: 'account', component: { template: '<h1>Konto</h1>' } },
      { path: '/admin', name: 'admin-overview', component: { template: '<h1>Admin-Center</h1>' } },
      { path: '/admin/users', name: 'admin-users', component: { template: '<h1>Nutzer</h1>' } },
      { path: '/admin/invitations', name: 'admin-invitations', component: { template: '<h1>Einladungen</h1>' } },
      { path: '/admin/security', name: 'admin-audit', component: { template: '<h1>Anmeldeprotokoll</h1>' } },
      { path: '/admin/system', name: 'admin-system', component: { template: '<h1>Systemstatus</h1>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [pinia, router] } })
  return { router, wrapper }
}

describe('App-Sidebar-Navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('trennt primäre und utility Navigation in der geforderten Reihenfolge', async () => {
    const { wrapper } = await mountApp('/erfolge')
    const navigationGroups = wrapper.findAll('aside nav')

    expect(navigationGroups).toHaveLength(2)
    expect(navigationGroups[0].findAll('a').map((link) => link.attributes('href'))).toEqual([
      '/',
      '/tage',
      '/wochen',
      '/wochentage',
      '/kalender',
      '/trends',
      '/mikronaehrstoffe',
      '/erfolge',
    ])
    expect(navigationGroups[1].findAll('a').map((link) => link.attributes('href'))).toEqual([
      '/importe',
      '/datenqualitaet',
      '/budgets-und-ziele',
      '/konto',
    ])
    expect(wrapper.findAll('aside a').filter((link) => link.classes('active'))).toHaveLength(1)
    expect(navigationGroups[0].get('a[href="/erfolge"]').classes()).toContain('active')
    expect(wrapper.findAll('aside nav a')).toHaveLength(12)
    wrapper.unmount()
  })

  it('shows administrators exactly one primary admin-console entry', async () => {
    const { wrapper } = await mountApp('/admin/users', true)

    const adminLinks = wrapper.findAll('aside a[href^="/admin"]')
    expect(adminLinks).toHaveLength(1)
    expect(adminLinks[0].text()).toMatch(/Admin/)
    expect(adminLinks[0].classes()).toContain('active')
    expect(wrapper.find('aside a[href="/admin/users"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not show an admin-console entry to normal users', async () => {
    const { wrapper } = await mountApp()
    expect(wrapper.find('aside a[href^="/admin"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders an aligned, non-interactive sidebar version row', async () => {
    const { wrapper } = await mountApp()
    const versionRow = wrapper.get('.sidebar-footer-version')

    expect(versionRow.get('small').text()).toBe(`CaloGraph v${packageMetadata.version}`)
    expect(versionRow.get('svg').attributes('aria-hidden')).toBe('true')
    expect(versionRow.element.tagName).toBe('DIV')
    expect(wrapper.get('.sidebar-signout').text().trim()).not.toBe('')
    wrapper.unmount()
  })

  it('schließt das mobile Menü auch bei einem Utility-Navigationseintrag', async () => {
    const { router, wrapper } = await mountApp()
    await wrapper.get('.menu-button').trigger('click')
    expect(wrapper.get('aside').classes()).toContain('open')

    await wrapper.get('.sidebar-utility-navigation a[href="/konto"]').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('account')
    expect(wrapper.get('aside').classes()).not.toContain('open')
    expect(wrapper.get('.sidebar-utility-navigation a[href="/konto"]').classes()).toContain('active')
    wrapper.unmount()
  })
})

describe('App-Brand-Navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders desktop and mobile brands as keyboard-reachable overview links', async () => {
    const { router, wrapper } = await mountApp()
    const brands = wrapper.findAll('a').filter((link) => link.text().trim() === 'CaloGraph')

    expect(brands).toHaveLength(2)
    expect(brands.map((link) => link.attributes('href'))).toEqual(['/', '/'])
    expect(brands.map((link) => link.element.tagName)).toEqual(['A', 'A'])

    await brands[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('overview')
    wrapper.unmount()
  })

  it('closes the mobile menu when the mobile brand navigates home', async () => {
    const { router, wrapper } = await mountApp()
    await wrapper.get('.menu-button').trigger('click')
    expect(wrapper.get('aside').classes()).toContain('open')

    await wrapper.get('a.mobile-brand').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('overview')
    expect(wrapper.get('aside').classes()).not.toContain('open')
    wrapper.unmount()
  })
})
