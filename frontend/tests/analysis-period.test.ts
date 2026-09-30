import { describe, expect, it } from 'vitest'

import { normalizeAnalysisPeriodForCapability, supportedAnalysisPeriods } from '../src/analysis-v2'
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

describe('capability-safe analysis period selection', () => {
  const today = '2026-03-31'

  it('offers only permitted quick presets and never offers the legacy 180-day option', () => {
    expect(supportedAnalysisPeriods(31, false)).toEqual(['7', '30'])
    expect(supportedAnalysisPeriods(365, true)).toEqual(['7', '30', '90', '365'])
    expect(supportedAnalysisPeriods(3661, true)).toEqual(['7', '30', '90', '365', 'all'])
    expect(supportedAnalysisPeriods(3661, false)).toEqual(['7', '30', '90', '365'])
  })

  it('clamps an unsupported preset to the largest available capability-safe preset', () => {
    expect(normalizeAnalysisPeriodForCapability('365', null, 31, today)).toEqual({
      period: '30', range: { start: '2026-03-02', end: today },
    })
    expect(normalizeAnalysisPeriodForCapability('all', null, 365, today)).toEqual({
      period: '365', range: { start: '2025-04-01', end: today },
    })
    expect(normalizeAnalysisPeriodForCapability('180', null, 3661, today)).toEqual({
      period: '365', range: { start: '2025-04-01', end: today },
    })
  })

  it('keeps an inclusive 31-day custom range across a leap day and DST boundary', () => {
    const range = { start: '2024-02-29', end: '2024-03-30' }
    expect(normalizeAnalysisPeriodForCapability('custom', range, 31, today)).toEqual({
      period: 'custom', range,
    })
  })

  it.each([
    { label: '32 inclusive days', range: { start: '2024-02-28', end: '2024-03-30' } },
    { label: 'impossible calendar date', range: { start: '2026-02-30', end: '2026-03-01' } },
    { label: 'reversed dates', range: { start: '2026-03-02', end: '2026-03-01' } },
    { label: 'future end', range: { start: '2026-03-30', end: '2026-04-01' } },
  ])('falls back to a valid bounded default for $label', ({ range }) => {
    expect(normalizeAnalysisPeriodForCapability('custom', range, 31, today)).toEqual({
      period: '30', range: { start: '2026-03-02', end: today },
    })
  })

  it('preserves a supported all-data range at the 3661-day reader limit', () => {
    const selection = normalizeAnalysisPeriodForCapability('all', null, 3661, today)
    expect(selection.period).toBe('all')
    expect(selection.range.end).toBe(today)
    expect(analysisRangeWithinDays(selection.range.start, selection.range.end, 3661)).toBe(true)
    expect(analysisRangeWithinDays(selection.range.start, selection.range.end, 3660)).toBe(false)
  })
})
