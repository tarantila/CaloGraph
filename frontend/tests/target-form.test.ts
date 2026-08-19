import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import { saveTargetDraft, type TargetDraft } from '../src/target-form'

beforeEach(() => {
  setLocale(DEFAULT_LOCALE)
})

function targetDraft(
  caloriesKcal: number,
  maintenanceKcal: number | null,
  activityMode: TargetDraft['activity_mode'] = 'off',
  activitySourceType: TargetDraft['activity_source_type'] = null,
): TargetDraft {
  return {
    valid_from: '2026-08-11',
    calories_kcal: caloriesKcal,
    maintenance_kcal: maintenanceKcal,
    protein_g: 140,
    carbs_g: null,
    fat_g: null,
    fiber_g: null,
    activity_mode: activityMode,
    activity_source_type: activitySourceType,
  }
}

describe('target form persistence', () => {
  beforeEach(() => {
    apiMock.mockReset()
    apiMock.mockResolvedValue({})
  })

  it.each([
    ['deficit', 2000, 2500],
    ['maintenance', 2500, 2500],
    ['surplus', 3000, 2500],
    ['without maintenance', 3000, null],
    ['minimal positive maintenance', 3000, 0.001],
  ])('accepts %s targets', async (_label, caloriesKcal, maintenanceKcal) => {
    await saveTargetDraft(targetDraft(caloriesKcal, maintenanceKcal))

    expect(apiMock).toHaveBeenCalledWith('/settings/targets', {
      method: 'POST',
      body: JSON.stringify({
        valid_from: '2026-08-11',
        calories_kcal: caloriesKcal,
        maintenance_kcal: maintenanceKcal,
        protein_g: 140,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
        activity_mode: 'off',
        activity_source_type: null,
      }),
    })
  })

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])(
    'rejects invalid maintenance value %s',
    async (maintenanceKcal) => {
      await expect(
        saveTargetDraft(targetDraft(3000, maintenanceKcal)),
      ).rejects.toThrow('Erhaltungsbedarf muss größer als 0 sein')
      expect(apiMock).not.toHaveBeenCalled()
    },
  )

  it('persists a selected activity source with full activity credit', async () => {
    await saveTargetDraft(targetDraft(2000, 2500, 'full', 'apple_health_xml'))

    expect(apiMock).toHaveBeenCalledWith('/settings/targets', {
      method: 'POST',
      body: JSON.stringify({
        valid_from: '2026-08-11',
        calories_kcal: 2000,
        maintenance_kcal: 2500,
        protein_g: 140,
        carbs_g: null,
        fat_g: null,
        fiber_g: null,
        activity_mode: 'full',
        activity_source_type: 'apple_health_xml',
      }),
    })
  })

  it('rejects full activity credit without a selected source', async () => {
    await expect(saveTargetDraft(targetDraft(2000, 2500, 'full'))).rejects.toThrow(
      'Für Aktivitätskalorien muss eine Quelle ausgewählt werden.',
    )
    expect(apiMock).not.toHaveBeenCalled()
  })
})
