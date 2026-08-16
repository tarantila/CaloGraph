import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({
  api: apiMock,
  localizeApiError: () => 'Die Anfrage konnte nicht verarbeitet werden.',
}))

import AchievementsView from '../src/views/AchievementsView.vue'
import { setLocale } from '../src/i18n'

describe('AchievementsView', () => {
  beforeEach(() => {
    apiMock.mockReset()
    setLocale('de')
  })

  it('zeigt Fortschritt, freigeschaltete Namen und gesperrte Hidden-Karten', async () => {
    apiMock.mockResolvedValue({
      achievements: [
        {
          key: 'tracked_7_days',
          category: 'tracking',
          kind: 'milestone',
          hidden: false,
          unlocked: false,
          unlocked_at: null,
          progress: 3,
          target: 7,
          sort_order: 20,
        },
        {
          key: 'first_day',
          category: 'tracking',
          kind: 'milestone',
          hidden: false,
          unlocked: true,
          unlocked_at: '2026-08-16T10:00:00Z',
          progress: null,
          target: 1,
          sort_order: 10,
        },
        {
          key: 'hidden_leap_day',
          category: 'hidden',
          kind: 'discovery',
          hidden: true,
          unlocked: false,
          sort_order: 410,
        },
      ],
    })

    const wrapper = mount(AchievementsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Lucky Seven')
    expect(wrapper.text()).toContain('First Step')
    expect(wrapper.text()).toContain('Versteckter Erfolg')
    expect(wrapper.text()).toContain('3 / 7')
    expect(wrapper.findAll('progress')).toHaveLength(1)
  })
})
