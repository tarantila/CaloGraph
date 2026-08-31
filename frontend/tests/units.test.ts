import { describe, expect, it } from 'vitest'

import {
  centimetersToFeetInches,
  feetInchesToCentimeters,
  kilogramsToPounds,
  poundsToKilograms,
  unitSystemToWeightUnit,
  weightUnitToUnitSystem,
} from '../src/units'
import { kgToWeight, weightToKg } from '../src/target-form'

describe('unit system conversions', () => {
  it('maps unit systems to the persisted weight unit contract', () => {
    expect(unitSystemToWeightUnit('metric')).toBe('kg')
    expect(unitSystemToWeightUnit('imperial')).toBe('lb')
    expect(weightUnitToUnitSystem('kg')).toBe('metric')
    expect(weightUnitToUnitSystem('lb')).toBe('imperial')
  })

  it('uses the shared target weight conversion factor', () => {
    expect(kilogramsToPounds(70)).toBe(kgToWeight(70, 'lb'))
    expect(poundsToKilograms(154.324)).toBeCloseTo(weightToKg(154.324, 'lb'), 10)
  })

  it('round-trips a canonical height through imperial display values', () => {
    expect(centimetersToFeetInches(172.72)).toEqual({ feet: 5, inches: 8 })
    expect(feetInchesToCentimeters(5, 8)).toBe(172.72)

    for (const centimeters of [150, 170, 172.5, 180, 199.99, 300]) {
      const imperial = centimetersToFeetInches(centimeters)
      expect(feetInchesToCentimeters(imperial.feet, imperial.inches)).toBeCloseTo(centimeters, 2)
    }
  })

  it('rejects invalid imperial height boundaries', () => {
    expect(feetInchesToCentimeters(0, 0)).toBeNull()
    expect(feetInchesToCentimeters(-1, 0)).toBeNull()
    expect(feetInchesToCentimeters(5, -0.1)).toBeNull()
    expect(feetInchesToCentimeters(5, 12)).toBeNull()
    expect(feetInchesToCentimeters(12, 0)).toBeNull()
    expect(feetInchesToCentimeters('5', '8')).toBe(172.72)
  })
})
