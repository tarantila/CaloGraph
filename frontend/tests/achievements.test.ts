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

  it('zeigt Fortschritt und mehrere gesperrte Hidden-Platzhalter', async () => {
    apiMock.mockResolvedValue({
      achievements: [
        {
          key: 'tracked_7_days',
          category: 'tracking',
          kind: 'milestone',
          hidden: false,
          placeholder: false,
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
          placeholder: false,
          unlocked: true,
          unlocked_at: '2026-08-16T10:00:00Z',
          progress: null,
          target: 1,
          sort_order: 10,
        },
        ...[240, 450, 460, 470].map((sort_order) => ({
          category: 'hidden',
          hidden: true,
          placeholder: true,
          unlocked: false,
          unlocked_at: null,
          progress: null,
          target: null,
          sort_order,
        })),
      ],
    })

    const wrapper = mount(AchievementsView)
    await flushPromises()

    expect(wrapper.text()).toContain('Lucky Seven')
    expect(wrapper.text()).toContain('First Step')
    expect(wrapper.text().match(/Versteckter Erfolg/g)).toHaveLength(4)
    expect(wrapper.text().match(/\?\?\?/g)).toHaveLength(4)
    expect(wrapper.findAll('.achievement-card.hidden')).toHaveLength(4)
    expect(wrapper.text()).toContain('3 / 7')
    expect(wrapper.findAll('progress')).toHaveLength(1)
  })

  it('löst ein freigeschaltetes Hidden Achievement mit echtem Inhalt auf', async () => {
    apiMock.mockResolvedValue({
      achievements: [
        {
          key: 'hidden_full_house',
          category: 'hidden',
          kind: 'discovery',
          icon: 'trophy',
          hidden: true,
          placeholder: false,
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
    expect(wrapper.text()).not.toContain('???')
    expect(wrapper.findAll('.achievement-card.hidden')).toHaveLength(0)
    expect(wrapper.findAll('progress')).toHaveLength(0)
  })

  it('reveals make-a-wish translations consistently after unlock', async () => {
    apiMock.mockResolvedValue({
      achievements: [{
        key: 'make_a_wish',
        category: 'hidden',
        kind: 'discovery',
        icon: 'calendar',
        hidden: true,
        placeholder: false,
        unlocked: true,
        unlocked_at: '2026-08-31T10:00:00Z',
        progress: null,
        target: null,
        sort_order: 470,
      }],
    })

    const german = mount(AchievementsView)
    await flushPromises()
    expect(german.text()).toContain('Wünsch dir was!')
    expect(german.text()).toContain('Ein bisschen Geburtstagszauber.')
    expect(german.text()).not.toContain('Versteckter Erfolg')
    german.unmount()

    setLocale('en')
    const english = mount(AchievementsView)
    await flushPromises()
    expect(english.text()).toContain('Make a Wish!')
    expect(english.text()).toContain('A little birthday magic.')
    expect(english.text()).not.toContain('???')
  })
})
