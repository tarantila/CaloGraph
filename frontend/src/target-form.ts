import { api } from './api'
import type { Target } from './types'

type TargetNumericValue = number | null | ''

export interface TargetDraft {
  valid_from: string
  calories_kcal: TargetNumericValue
  maintenance_kcal: TargetNumericValue
  protein_g: TargetNumericValue
  carbs_g: TargetNumericValue
  fat_g: TargetNumericValue
  fiber_g: TargetNumericValue
}

export const TARGET_LIMITS = {
  caloriesMin: 1,
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
  }
}

function requireFinite(value: TargetNumericValue, message: string): number {
  if (value === null || value === '' || !Number.isFinite(value)) {
    throw new TargetValidationError(message)
  }
  return value
}

function normalizeOptionalNutrient(
  value: TargetNumericValue,
  label: string,
): number | null {
  if (value === null || value === '') return null
  if (!Number.isFinite(value) || value < TARGET_LIMITS.nutrientMin) {
    throw new TargetValidationError(`${label} darf nicht negativ sein.`)
  }
  return value
}

export async function saveTargetDraft(
  target: TargetDraft,
  existingTargets: Pick<Target, 'valid_from'>[] = [],
): Promise<void> {
  if (!target.valid_from) {
    throw new TargetValidationError('Das Gültigkeitsdatum konnte nicht bestimmt werden.')
  }
  const caloriesKcal = requireFinite(
    target.calories_kcal,
    'Bitte gib dein tägliches Kalorienbudget ein.',
  )
  if (caloriesKcal < TARGET_LIMITS.caloriesMin) {
    throw new TargetValidationError('Das Kalorienbudget muss größer als 0 sein.')
  }
  const proteinG = requireFinite(
    target.protein_g,
    'Bitte gib dein tägliches Proteinziel ein.',
  )
  if (proteinG < TARGET_LIMITS.nutrientMin) {
    throw new TargetValidationError('Das Proteinziel darf nicht negativ sein.')
  }
  const maintenanceKcal =
    target.maintenance_kcal === '' ? null : target.maintenance_kcal
  if (
    maintenanceKcal !== null
    && (
      !Number.isFinite(maintenanceKcal)
      || maintenanceKcal < caloriesKcal
    )
  ) {
    throw new TargetValidationError(
      'Der Erhaltungsbedarf darf nicht unter dem Kalorienbudget liegen.',
    )
  }
  const carbsG = normalizeOptionalNutrient(target.carbs_g, 'Das Kohlenhydratziel')
  const fatG = normalizeOptionalNutrient(target.fat_g, 'Das Fettziel')
  const fiberG = normalizeOptionalNutrient(target.fiber_g, 'Das Ballaststoffziel')

  const payload = {
    valid_from: target.valid_from,
    calories_kcal: caloriesKcal,
    maintenance_kcal: maintenanceKcal,
    protein_g: proteinG,
    carbs_g: carbsG,
    fat_g: fatG,
    fiber_g: fiberG,
  }
  const existing = existingTargets.some((item) => item.valid_from === target.valid_from)
  await api(existing ? `/settings/targets/${target.valid_from}` : '/settings/targets', {
    method: existing ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  })
}
