import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'

import AccountLayout from '../src/views/AccountLayout.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

const accountChildren = [
  { path: 'persoenliche-daten', name: 'account-personal', component: { template: '<h1>Persönliche Daten</h1>' } },
  { path: 'budgets-und-ziele', name: 'account-targets', component: { template: '<h1>Budgets &amp; Ziele</h1>' } },
  { path: 'importe', name: 'account-imports', component: { template: '<h1>Importe</h1>' } },
  { path: 'datenstatus', name: 'account-data-status', component: { template: '<h1>Datenstatus</h1>' } },
  { path: 'integrationen', name: 'account-integrations', component: { template: '<h1>Integrationen</h1>' } },
  { path: 'daten-und-datenschutz', name: 'account-data-privacy', component: { template: '<h1>Daten &amp; Datenschutz</h1>' } },
  { path: 'allgemeine-einstellungen', name: 'account-general', component: { template: '<h1>Allgemeine Einstellungen</h1>' } },
  { path: 'sicherheit', name: 'account-security', component: { template: '<h1>Sicherheit</h1>' } },
]

async function mountAccount(path = '/konto/integrationen') {
  setLocale(DEFAULT_LOCALE)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{
      path: '/konto',
      component: AccountLayout,
      children: accountChildren,
    }],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  return { router, wrapper }
}

async function mountAccountFromOutside() {
  setLocale(DEFAULT_LOCALE)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/ausserhalb', name: 'outside', component: { template: '<h1>Außerhalb</h1>' } },
      {
        path: '/konto',
        component: AccountLayout,
        children: accountChildren,
      },
    ],
  })
  await router.push('/ausserhalb')
  await router.isReady()
  const wrapper = mount({ template: '<RouterView />' }, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  await router.push('/konto/sicherheit')
  await flushPromises()
  return { router, wrapper }
}

describe('AccountLayout navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
    setLocale(DEFAULT_LOCALE)
  })

  it('renders grouped desktop links and grouped mobile options with route-derived active state', async () => {
    const { wrapper } = await mountAccount('/konto/integrationen')

    expect(wrapper.findAll('.account-navigation-group')).toHaveLength(3)
    expect(wrapper.findAll('.account-navigation-group')[0].text()).toContain('DEIN KONTO')
    expect(wrapper.findAll('.account-navigation-group')[1].text()).toContain('DATEN')
    expect(wrapper.findAll('.account-navigation-group')[2].text()).toContain('APP')

    const links = wrapper.findAll('.account-navigation a')
    expect(wrapper.get('.account-navigation-nav').attributes('tabindex')).toBe('0')
    expect(links).toHaveLength(8)
    expect(wrapper.get('.account-navigation h1').text()).toBe('Einstellungen')
    expect(wrapper.findAll('.account-navigation a svg')).toHaveLength(8)
    expect(links.filter((link) => link.classes('active'))).toHaveLength(1)
    expect(wrapper.get('a[href="/konto/integrationen"]').classes()).toContain('active')
    expect(wrapper.get('a[href="/konto/integrationen"]').attributes('aria-current')).toBe('page')

    const groups = wrapper.findAll('select[name="account-section"] optgroup')
    expect(groups).toHaveLength(3)
    expect(groups.map((group) => group.findAll('option').map((option) => option.attributes('value')))).toEqual([
      ['account-personal', 'account-security', 'account-targets'],
      ['account-imports', 'account-data-status', 'account-integrations', 'account-data-privacy'],
      ['account-general'],
    ])
    expect(wrapper.get<HTMLSelectElement>('select[name="account-section"]').element.value).toBe('account-integrations')
    wrapper.unmount()
  })

  it('assigns Account content widths by page type', async () => {
    const { router, wrapper } = await mountAccount()
    const expectedWidths = [
      ['/konto/persoenliche-daten', 'account-content--comfortable'],
      ['/konto/budgets-und-ziele', 'account-content--wide'],
      ['/konto/importe', 'account-content--wide'],
      ['/konto/datenstatus', 'account-content--wide'],
      ['/konto/integrationen', 'account-content--comfortable'],
      ['/konto/daten-und-datenschutz', 'account-content--comfortable'],
      ['/konto/allgemeine-einstellungen', 'account-content--comfortable'],
      ['/konto/sicherheit', 'account-content--compact'],
    ] as const

    for (const [path, widthClass] of expectedWidths) {
      await router.push(path)
      await flushPromises()
      expect(wrapper.get('.account-content').classes()).toContain(widthClass)
    }
    wrapper.unmount()
  })

  it('focuses the child heading after entering the account center', async () => {
    const { router, wrapper } = await mountAccountFromOutside()

    expect(router.currentRoute.value.name).toBe('account-security')
    expect(wrapper.get('.account-content h1').text()).toBe('Sicherheit')
    expect(document.activeElement).toBe(wrapper.get('.account-content h1').element)
    wrapper.unmount()
  })

  it('navigates from the mobile select and focuses the new child heading', async () => {
    const { router, wrapper } = await mountAccount('/konto/persoenliche-daten')
    const select = wrapper.get<HTMLSelectElement>('select[name="account-section"]')

    await select.setValue('account-security')
    await flushPromises()

    expect(router.currentRoute.value.name).toBe('account-security')
    expect(select.element.value).toBe('account-security')
    expect(wrapper.get('a[href="/konto/sicherheit"]').classes()).toContain('active')
    expect(document.activeElement).toBe(wrapper.get('.account-content h1').element)
    wrapper.unmount()
  })
})
