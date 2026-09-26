import { describe, expect, it } from 'vitest'

import { analysisRangeWithinDays } from '../src/analytics-period'

describe('analysis period bounds', () => {
  it('counts inclusive calendar days across leap dates and DST without elapsed-time math', () => {
    expect(analysisRangeWithinDays('2026-03-01', '2026-03-31', 31)).toBe(true)
    expect(analysisRangeWithinDays('2026-03-01', '2026-04-01', 31)).toBe(false)
    expect(analysisRangeWithinDays('2026-03-28', '2026-03-30', 31)).toBe(true)
    expect(analysisRangeWithinDays('2024-02-29', '2024-03-01', 31)).toBe(true)
    expect(analysisRangeWithinDays('2026-02-30', '2026-03-01', 31)).toBe(false)
  })

  it('rejects malformed, inverted, and invalid bounds', () => {
    expect(analysisRangeWithinDays('2026-03-01', '2026-03-01', 1)).toBe(true)
    expect(analysisRangeWithinDays('2026-03-02', '2026-03-01', 31)).toBe(false)
    expect(analysisRangeWithinDays('2026-03-01', '2026-03-01', 0)).toBe(false)
    expect(analysisRangeWithinDays('2026-03-01', '2026-03-01', 1.5)).toBe(false)
    expect(analysisRangeWithinDays(null, '2026-03-01', 31)).toBe(false)
  })
})
