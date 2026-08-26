import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import AdminLayout from '../src/views/AdminLayout.vue'
import { setLocale } from '../src/i18n'

const routes = [
  {
    path: '/admin',
    component: AdminLayout,
    children: [
      { path: '', name: 'admin-overview', component: { template: '<p>Overview content</p>' } },
      { path: 'users', name: 'admin-users', component: { template: '<p>Users content</p>' } },
      { path: 'invitations', name: 'admin-invitations', component: { template: '<p>Invitations content</p>' } },
      { path: 'security', name: 'admin-audit', component: { template: '<p>Audit content</p>' } },
      { path: 'system', name: 'admin-system', component: { template: '<p>System content</p>' } },
      { path: 'logs', name: 'admin-logs', component: { template: '<p>Logs content</p>' } },
      { path: 'backups', name: 'admin-backups', component: { template: '<p>Backups content</p>' } },
    ],
  },
]

async function mountLayout(path: string) {
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(path)
  await router.isReady()
  return mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
}

describe('admin console layout', () => {
  it.each([
    ['/admin', 'admin-overview', 'Overview content'],
    ['/admin/users', 'admin-users', 'Users content'],
    ['/admin/invitations', 'admin-invitations', 'Invitations content'],
    ['/admin/security', 'admin-audit', 'Audit content'],
    ['/admin/system', 'admin-system', 'System content'],
    ['/admin/logs', 'admin-logs', 'Logs content'],
    ['/admin/backups', 'admin-backups', 'Backups content'],
  ])('keeps the admin layout on %s', async (path, activeName, content) => {
    const wrapper = await mountLayout(path)

    expect(wrapper.find('.admin-console').exists()).toBe(true)
    expect(wrapper.text()).toContain(content)
    expect(wrapper.get(`.admin-sidebar-nav a[href="${path}"]`).classes()).toContain('active')
    expect(wrapper.findAll('.admin-sidebar-nav a')).toHaveLength(7)
    wrapper.unmount()
  })

  it('keeps grouping labels separate from the seven keyboard-scrollable navigation targets', async () => {
    setLocale('de')
    const wrapper = await mountLayout('/admin/system')
    const navigation = wrapper.get('.admin-sidebar-nav')

    expect(navigation.attributes('tabindex')).toBe('0')
    expect(navigation.findAll('a').map((link) => link.text().trim())).toEqual([
      'Übersicht',
      'Benutzer',
      'Einladungen',
      'Anmeldeprotokoll',
      'Systemstatus',
      'App-Logs',
      'Backups',
    ])
    expect(navigation.findAll('.admin-sidebar-label').map((label) => label.text())).toEqual([
      'Verwaltung',
      'System',
    ])
    expect(navigation.get('a[href="/admin/system"]').attributes('aria-current')).toBe('page')
    wrapper.unmount()
  })
})
