import { beforeEach, describe, expect, it, vi } from 'vitest'

const { apiMock } = vi.hoisted(() => ({ apiMock: vi.fn() }))
vi.mock('../src/api', () => ({ api: apiMock }))

import { DEFAULT_LOCALE, setLocale } from '../src/i18n'
import {
  saveTargetDraft,
  targetWeightFromTarget,
  targetWeightPayload,
  weightToKg,
  type TargetDraft,
} from '../src/target-form'

beforeEach(() => {
  setLocale(DEFAULT_LOCALE)
})

const INVALID_REMOTE_VALUES = [
  '',
  'NaN',
  'Infinity',
  '-Infinity',
  'abc',
  Number.NaN,
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
] as const

describe('target weight semantics', () => {
  it('maps canonical pairs to none, exact, and range modes', () => {
    expect(targetWeightFromTarget({ target_weight_min_kg: null, target_weight_max_kg: null })).toEqual({
      mode: 'none',
      minKg: null,
      maxKg: null,
    })
    expect(targetWeightFromTarget({ target_weight_min_kg: 75, target_weight_max_kg: 75 })).toMatchObject({ mode: 'exact', minKg: 75, maxKg: 75 })
    expect(targetWeightFromTarget({ target_weight_min_kg: 70, target_weight_max_kg: 80 })).toMatchObject({ mode: 'range', minKg: 70, maxKg: 80 })
    expect(targetWeightFromTarget({ target_weight_min_kg: '70.000', target_weight_max_kg: '80.000' })).toMatchObject({ mode: 'range', minKg: 70, maxKg: 80 })
  })

  it.each(INVALID_REMOTE_VALUES)(
    'rejects invalid remote minimum %s',
    (value) => {
      expect(() => targetWeightFromTarget({
        target_weight_min_kg: value,
        target_weight_max_kg: '80',
      })).toThrow()
    },
  )

  it.each(INVALID_REMOTE_VALUES)(
    'rejects invalid remote maximum %s',
    (value) => {
      expect(() => targetWeightFromTarget({
        target_weight_min_kg: '80',
        target_weight_max_kg: value,
      })).toThrow()
    },
  )

  it('rejects invalid loaded pairs and unordered ranges', () => {
    expect(() => targetWeightFromTarget({
      target_weight_min_kg: undefined,
      target_weight_max_kg: undefined,
    } as never)).toThrow()
    expect(() => targetWeightFromTarget({ target_weight_min_kg: 80, target_weight_max_kg: null })).toThrow()
    expect(() => targetWeightPayload({
      target_weight_mode: 'range',
      target_weight_min_kg: 80,
      target_weight_max_kg: 70,
    })).toThrow('Das untere Zielgewicht muss kleiner als das obere sein')
  })

  it('converts lb input and rounds only the saved kg payload', () => {
    const pounds = 165.347
    const expected = Math.round(weightToKg(pounds, 'lb') * 1000) / 1000
    expect(targetWeightPayload({
      target_weight_mode: 'exact',
      target_weight_min_kg: pounds,
      target_weight_max_kg: pounds,
    }, 'lb')).toEqual({ target_weight_min_kg: expected, target_weight_max_kg: expected })
  })

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
    target_weight_mode: 'none',
    target_weight_min_kg: null,
    target_weight_max_kg: null,
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
        target_weight_min_kg: null,
        target_weight_max_kg: null,
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
        target_weight_min_kg: null,
        target_weight_max_kg: null,
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
