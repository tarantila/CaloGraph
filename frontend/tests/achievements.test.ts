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
          category: 'hidden',
          hidden: true,
          unlocked: false,
          unlocked_at: null,
          progress: null,
          target: null,
          sort_order: 410,
        },
        {
          category: 'hidden',
          hidden: true,
          unlocked: false,
          unlocked_at: null,
          progress: null,
          target: null,
          sort_order: 420,
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
    expect(wrapper.text().match(/\?\?\?/g)).toHaveLength(2)
  })

  it('löst ein freigeschaltetes Hidden Achievement mit echtem Key auf', async () => {
    apiMock.mockResolvedValue({
      achievements: [
        {
          key: 'hidden_full_house',
          category: 'hidden',
          kind: 'discovery',
          hidden: true,
          unlocked: true,
          unlocked_at: '2026-08-16T10:00:00Z',
          progress: null,
          target: null,
          sort_order: 440,
        },
      ],
    })

    const wrapper = mount(AchievementsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Full House')
    expect(wrapper.text()).not.toContain('Versteckter Erfolg')
    expect(wrapper.findAll('progress')).toHaveLength(0)
  })
})
