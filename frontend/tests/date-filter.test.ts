import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DateFilter from '../src/components/DateFilter.vue'

describe('DateFilter', () => {
  it('emits reproducible date values', async () => {
    const wrapper = mount(DateFilter, { props: { start: '2024-01-01', end: '2024-01-31' } })
    await wrapper.findAll('input')[0].setValue('2024-01-02')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('update:start')?.[0]).toEqual(['2024-01-02'])
    expect(wrapper.emitted('apply')).toHaveLength(1)
  })
})

