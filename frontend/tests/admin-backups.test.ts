import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AdminBackupsView from '../src/views/AdminBackupsView.vue'
import { setLocale } from '../src/i18n'

describe('admin backups view', () => {
  it('renders read-only backup information without operational controls', () => {
    setLocale('de')
    const wrapper = mount(AdminBackupsView)

    expect(wrapper.text()).toContain('PostgreSQL + age')
    expect(wrapper.text()).toContain('Hostseitig')
    expect(wrapper.text()).toContain('Nicht durch CaloGraph überwacht')
    expect(wrapper.text()).toContain('Betreiber-Richtlinie')
    expect(wrapper.text()).toContain('außerhalb der Webanwendung')
    expect(wrapper.findAll('.backup-status-item')).toHaveLength(4)
    expect(wrapper.findAll('h2')).toHaveLength(0)
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.findAll('input')).toHaveLength(0)
    const documentation = wrapper.get('a[href="https://github.com/tarantila/CaloGraph/blob/main/docs/backup-restore.md"]')
    expect(documentation.attributes('target')).toBe('_blank')
    expect(documentation.attributes('rel')).toBe('noopener noreferrer')
    wrapper.unmount()
  })
})
