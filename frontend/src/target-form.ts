import { api } from './api'
import { i18n } from './i18n'
import type { ActivityMode, ActivitySourceType, DecimalTransport, Target } from './types'
import type { PreferredWeightUnit } from './composables/useProfilePreferences'

type TargetNumericValue = number | null | ''

export type TargetWeightMode = 'none' | 'exact' | 'range'

export const TARGET_WEIGHT_LB_PER_KG = 2.2046226218487757

export const TARGET_WEIGHT_LIMITS = {
  minKg: 0,
  maxKg: 1000,
} as const

export interface TargetWeightDraft {
  mode: TargetWeightMode
  minKg: TargetNumericValue
  maxKg: TargetNumericValue
}

export interface TargetWeightPair {
  target_weight_min_kg: number | null
  target_weight_max_kg: number | null
}

export interface TargetDraft {
  valid_from: string
  calories_kcal: TargetNumericValue
  maintenance_kcal: TargetNumericValue
  protein_g: TargetNumericValue
  carbs_g: TargetNumericValue
  fat_g: TargetNumericValue
  fiber_g: TargetNumericValue
  target_weight_mode: TargetWeightMode
  target_weight_min_kg: TargetNumericValue
  target_weight_max_kg: TargetNumericValue
  activity_mode: ActivityMode
  activity_source_type: ActivitySourceType | null
}

export const TARGET_LIMITS = {
  caloriesMin: 1,
  maintenanceMin: 0.001,
  nutrientMin: 0,
} as const

export class TargetValidationError extends Error {}
export class TargetWeightLoadError extends Error {}

export function createEmptyTargetDraft(): TargetDraft {
  return {
    valid_from: '',
    calories_kcal: null,
    maintenance_kcal: null,
    protein_g: null,
    carbs_g: null,
    fat_g: null,
    fiber_g: null,
    target_weight_mode: 'none',
    target_weight_min_kg: null,
    target_weight_max_kg: null,
    activity_mode: 'off',
    activity_source_type: null,
  }
}

export function roundTargetWeightKg(value: number): number {
  return Math.round((value + Number.EPSILON) * 1000) / 1000
}
export function weightToKg(value: number, unit: PreferredWeightUnit): number {
  return unit === 'lb' ? value / TARGET_WEIGHT_LB_PER_KG : value
}

export function kgToWeight(value: number, unit: PreferredWeightUnit): number {
  return unit === 'lb' ? value * TARGET_WEIGHT_LB_PER_KG : value
}

export function displayTargetWeight(value: number, unit: PreferredWeightUnit): number {
  const fractionDigits = unit === 'lb' ? 1 : 3
  const factor = 10 ** fractionDigits
  return Math.round((kgToWeight(value, unit) + Number.EPSILON) * factor) / factor
}

export function formatTargetWeight(value: number, unit: PreferredWeightUnit): string {
  return new Intl.NumberFormat(i18n.global.locale.value, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(kgToWeight(value, unit))
}

function targetWeightNumber(value: DecimalTransport): number | null {
  if (value === null) return null
  if (typeof value === 'string' && value.trim() !== '') value = Number(value)
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TargetWeightLoadError('Invalid target weight')
  }
  return value
}

export function targetWeightFromTarget(
  target: Pick<Target, 'target_weight_min_kg' | 'target_weight_max_kg'>,
): TargetWeightDraft {
  if (target.target_weight_min_kg === null && target.target_weight_max_kg === null) {
    return { mode: 'none', minKg: null, maxKg: null }
  }
  const min = targetWeightNumber(target.target_weight_min_kg)
  const max = targetWeightNumber(target.target_weight_max_kg)
  if (
    min === null ||
    max === null ||
    min <= TARGET_WEIGHT_LIMITS.minKg ||
    max <= TARGET_WEIGHT_LIMITS.minKg ||
    min > TARGET_WEIGHT_LIMITS.maxKg ||
    max > TARGET_WEIGHT_LIMITS.maxKg ||
    min > max
  ) {
    throw new TargetWeightLoadError('Invalid target weight')
  }
  if (min === max) return { mode: 'exact', minKg: min, maxKg: min }
  return { mode: 'range', minKg: min, maxKg: max }
}

export function targetWeightPayload(
  draft: Pick<TargetDraft, 'target_weight_mode' | 'target_weight_min_kg' | 'target_weight_max_kg'>,
  unit: PreferredWeightUnit = 'kg',
): TargetWeightPair {
  if (draft.target_weight_mode === 'none') {
    return { target_weight_min_kg: null, target_weight_max_kg: null }
  }
  const minInput = draft.target_weight_min_kg
  const maxInput = draft.target_weight_max_kg
  if (
    minInput === null ||
    minInput === '' ||
    !Number.isFinite(minInput) ||
    maxInput === null ||
    maxInput === '' ||
    !Number.isFinite(maxInput)
  ) {
    throw new TargetValidationError(i18n.global.t('targetForm.weightRequired'))
  }
  const minKg = roundTargetWeightKg(weightToKg(minInput, unit))
  const maxKg = roundTargetWeightKg(weightToKg(maxInput, unit))
  if (
    minKg <= TARGET_WEIGHT_LIMITS.minKg ||
    maxKg <= TARGET_WEIGHT_LIMITS.minKg ||
    minKg > TARGET_WEIGHT_LIMITS.maxKg ||
    maxKg > TARGET_WEIGHT_LIMITS.maxKg
  ) {
    throw new TargetValidationError(i18n.global.t('targetForm.weightRange'))
  }
  if (draft.target_weight_mode === 'exact' && minKg !== maxKg) {
    throw new TargetValidationError(i18n.global.t('targetForm.weightExact'))
  }
  if (draft.target_weight_mode === 'range' && minKg >= maxKg) {
    throw new TargetValidationError(i18n.global.t('targetForm.weightOrdered'))
  }
  return draft.target_weight_mode === 'exact'
    ? { target_weight_min_kg: minKg, target_weight_max_kg: minKg }
    : { target_weight_min_kg: minKg, target_weight_max_kg: maxKg }
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
  weightUnit: PreferredWeightUnit = 'kg',
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

  const weight = targetWeightPayload(target, weightUnit)
  const payload = {
    valid_from: target.valid_from,
    calories_kcal: caloriesKcal,
    maintenance_kcal: maintenanceKcal,
    protein_g: proteinG,
    carbs_g: carbsG,
    fat_g: fatG,
    fiber_g: fiberG,
    ...weight,
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
