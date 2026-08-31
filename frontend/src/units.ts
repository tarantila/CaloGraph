import {
  kgToWeight,
  weightToKg,
} from './target-form'
import type { PreferredWeightUnit } from './composables/useProfilePreferences'

export type UnitSystem = 'metric' | 'imperial'

export interface ImperialHeight {
  feet: number
  inches: number
}

const CENTIMETERS_PER_INCH = 2.54

export function unitSystemToWeightUnit(system: UnitSystem): PreferredWeightUnit {
  return system === 'imperial' ? 'lb' : 'kg'
}

export function weightUnitToUnitSystem(unit: PreferredWeightUnit): UnitSystem {
  return unit === 'lb' ? 'imperial' : 'metric'
}

export function kilogramsToPounds(valueKg: number): number {
  return kgToWeight(valueKg, 'lb')
}

export function poundsToKilograms(valueLb: number): number {
  return weightToKg(valueLb, 'lb')
}

export function roundHeightCentimeters(value: number): number {
  return Math.round((value + Number.EPSILON) * 100) / 100
}

export function centimetersToFeetInches(valueCm: number): ImperialHeight {
  const totalInches = Math.round((valueCm / CENTIMETERS_PER_INCH) * 1000) / 1000
  let feet = Math.floor(totalInches / 12)
  let inches = Math.round((totalInches - feet * 12) * 1000) / 1000
  if (inches >= 12) {
    feet += 1
    inches = 0
  }
  return { feet, inches }
}

export function feetInchesToCentimeters(feetValue: unknown, inchesValue: unknown): number | null {
  const feet = Number(feetValue)
  const inches = Number(inchesValue)
  if (
    !Number.isInteger(feet)
    || !Number.isFinite(inches)
    || feet < 0
    || inches < 0
    || inches >= 12
  ) return null
  const centimeters = (feet * 12 + inches) * CENTIMETERS_PER_INCH
  if (!Number.isFinite(centimeters) || centimeters <= 0 || centimeters > 300) return null
  return roundHeightCentimeters(centimeters)
}
