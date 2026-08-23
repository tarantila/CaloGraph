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

async function mountApp(path = '/tage') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const auth = useAuthStore()
  auth.user = user
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

  it('renders the sidebar version from frontend/package.json', async () => {
    const { wrapper } = await mountApp()

    expect(wrapper.get('.sidebar-footer small').text()).toBe(`CaloGraph v${packageMetadata.version}`)
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
