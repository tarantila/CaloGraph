import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import StatusBadge from '../src/components/StatusBadge.vue'

describe('StatusBadge', () => {
  it('renders a textual status in addition to color', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'probably_incomplete' } })
    expect(wrapper.text()).toContain('Kalorienwert fehlt')
    expect(wrapper.classes()).toContain('probably-incomplete')
  })
})
