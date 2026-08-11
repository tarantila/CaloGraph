import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import DateFilter from '../src/components/DateFilter.vue'
import { useAuthStore } from '../src/stores/auth'

describe('DateFilter', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('emits reproducible date values', async () => {
    const wrapper = mount(DateFilter, { props: { start: '2024-01-01', end: '2024-01-31' } })
    await wrapper.find('input[type="text"]').setValue('02.01.2024')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('update:start')?.[0]).toEqual(['2024-01-02'])
    expect(wrapper.emitted('apply')).toHaveLength(1)
  })

  it('does not apply the previous range when manual input is invalid', async () => {
    const wrapper = mount(DateFilter, { props: { start: '2024-01-01', end: '2024-01-31' } })
    await wrapper.find('input[type="text"]').setValue('31.02.2024')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('update:start')).toBeUndefined()
    expect(wrapper.emitted('apply')).toBeUndefined()
  })

  it('uses the account timezone for preset calendar boundaries', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-08-11T00:30:00Z'))
    useAuthStore().user = {
      id: 'user-1',
      username: 'owner',
      language: 'de',
      timezone: 'America/Los_Angeles',
      week_starts_on: 0,
      raw_payload_retention_days: 0,
      is_admin: false,
      is_active: true,
      deactivated_at: null,
    }
    const wrapper = mount(DateFilter, { props: { start: '2024-01-01', end: '2024-01-31' } })

    await wrapper.get('select').setValue('30')

    expect(wrapper.emitted('update:start')?.at(-1)).toEqual(['2026-07-12'])
    expect(wrapper.emitted('update:end')?.at(-1)).toEqual(['2026-08-10'])
  })
})

