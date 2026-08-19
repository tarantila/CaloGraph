import { api } from './api'
import { i18n } from './i18n'
import type { ActivityMode, ActivitySourceType, Target } from './types'

type TargetNumericValue = number | null | ''

export interface TargetDraft {
  valid_from: string
  calories_kcal: TargetNumericValue
  maintenance_kcal: TargetNumericValue
  protein_g: TargetNumericValue
  carbs_g: TargetNumericValue
  fat_g: TargetNumericValue
  fiber_g: TargetNumericValue
  activity_mode: ActivityMode
  activity_source_type: ActivitySourceType | null
}

export const TARGET_LIMITS = {
  caloriesMin: 1,
  maintenanceMin: 0.001,
  nutrientMin: 0,
} as const

export class TargetValidationError extends Error {}

export function createEmptyTargetDraft(): TargetDraft {
  return {
    valid_from: '',
    calories_kcal: null,
    maintenance_kcal: null,
    protein_g: null,
    carbs_g: null,
    fat_g: null,
    fiber_g: null,
    activity_mode: 'off',
    activity_source_type: null,
  }
}

function requireFinite(value: TargetNumericValue, messageKey: string): number {
  if (value === null || value === '' || !Number.isFinite(value)) {
    throw new TargetValidationError(i18n.global.t(messageKey))
  }
  return value
}

function normalizeOptionalNutrient(
  value: TargetNumericValue,
  labelKey: string,
): number | null {
  if (value === null || value === '') return null
  if (!Number.isFinite(value) || value < TARGET_LIMITS.nutrientMin) {
    throw new TargetValidationError(
      i18n.global.t('targetForm.nutrientNegative', { label: i18n.global.t(labelKey) }),
    )
  }
  return value
}

function normalizeOptionalPositive(
  value: TargetNumericValue,
  labelKey: string,
): number | null {
  if (value === null || value === '') return null
  if (!Number.isFinite(value) || value <= 0) {
    throw new TargetValidationError(
      i18n.global.t('targetForm.maintenancePositive', { label: i18n.global.t(labelKey) }),
    )
  }
  return value
}

export async function saveTargetDraft(
  target: TargetDraft,
  existingTargets: Pick<Target, 'valid_from'>[] = [],
): Promise<void> {
  if (!target.valid_from) {
    throw new TargetValidationError(i18n.global.t('targetForm.dateMissing'))
  }
  const caloriesKcal = requireFinite(
    target.calories_kcal,
    'targetForm.caloriesRequired',
  )
  if (caloriesKcal < TARGET_LIMITS.caloriesMin) {
    throw new TargetValidationError(i18n.global.t('targetForm.caloriesPositive'))
  }
  const proteinG = requireFinite(
    target.protein_g,
    'targetForm.proteinRequired',
  )
  if (proteinG < TARGET_LIMITS.nutrientMin) {
    throw new TargetValidationError(i18n.global.t('targetForm.proteinNonNegative'))
  }
  const maintenanceKcal = normalizeOptionalPositive(
    target.maintenance_kcal,
    'targetForm.maintenanceLabel',
  )
  const carbsG = normalizeOptionalNutrient(target.carbs_g, 'targetForm.nutrientLabelCarbs')
  const fatG = normalizeOptionalNutrient(target.fat_g, 'targetForm.nutrientLabelFat')
  const fiberG = normalizeOptionalNutrient(target.fiber_g, 'targetForm.nutrientLabelFiber')
  if (target.activity_mode === 'full' && target.activity_source_type === null) {
    throw new TargetValidationError(i18n.global.t('activity.sourceRequired'))
  }

  const payload = {
    valid_from: target.valid_from,
    calories_kcal: caloriesKcal,
    maintenance_kcal: maintenanceKcal,
    protein_g: proteinG,
    carbs_g: carbsG,
    fat_g: fatG,
    fiber_g: fiberG,
    activity_mode: target.activity_mode,
    activity_source_type:
      target.activity_mode === 'full' ? target.activity_source_type : null,
  }
  const existing = existingTargets.some((item) => item.valid_from === target.valid_from)
  await api(existing ? `/settings/targets/${target.valid_from}` : '/settings/targets', {
    method: existing ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  })
}
