import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'

import StatusBadge from '../src/components/StatusBadge.vue'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

beforeEach(() => {
  setLocale(DEFAULT_LOCALE)
})
describe('StatusBadge', () => {
  it('renders a textual status in addition to color', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'probably_incomplete' } })
    expect(wrapper.text()).toContain('Kalorienwert fehlt')
    expect(wrapper.classes()).toContain('probably-incomplete')
  })

  it('labels a partially persisted import clearly', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'partial_failed' } })
    expect(wrapper.text()).toContain('Teilweise importiert')
    expect(wrapper.classes()).toContain('partial-failed')
  })
})
