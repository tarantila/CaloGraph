import { describe, expect, it } from 'vitest'

import {
  analysisRangeWithinDays,
  commonAllDataRange,
  periodFromQuery,
  rangeForDays,
  resolveAllDataRange,
  supportedPeriodPresets,
  validPeriodRange,
} from '../src/analytics-period'

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

describe('reader-safe period selection', () => {
  const limits = { minDays: 1, maxDays: 31, customRange: true }
  const yes = { supports_all_data: true, all_data_start_date: '2024-02-29' }
  const no = { supports_all_data: false, all_data_start_date: null }

  it('requires an explicit full boundary and rejects 32 days rather than clipping to 31', () => {
    expect(resolveAllDataRange(undefined, '2024-03-30', limits)).toBeNull()
    expect(resolveAllDataRange(null, '2024-03-30', limits)).toBeNull()
    expect(resolveAllDataRange(no, '2024-03-30', limits)).toBeNull()
    expect(resolveAllDataRange({ ...yes, supports_all_data: false }, '2024-03-30', limits)).toBeNull()
    expect(resolveAllDataRange({ ...yes, all_data_start_date: null }, '2024-03-30', limits)).toBeNull()
    expect(resolveAllDataRange(yes, '2024-03-30', limits)).toEqual({
      start: '2024-02-29', end: '2024-03-30',
    })
    expect(resolveAllDataRange(yes, '2024-03-31', limits)).toBeNull()
  })

  it.each(['2024-02-30', '2024-04-01', 'not-a-date'])(
    'rejects the invalid full boundary %s',
    (start) => {
      expect(resolveAllDataRange({ ...yes, all_data_start_date: start }, '2024-03-30', limits)).toBeNull()
    },
  )

  it('uses rolling inclusive six months and year across calendar years, leap day and DST', () => {
    expect(rangeForDays('2026-10-01', 180)).toEqual({ start: '2026-04-05', end: '2026-10-01' })
    expect(rangeForDays('2024-03-30', 365)).toEqual({ start: '2023-04-01', end: '2024-03-30' })
    expect(supportedPeriodPresets(limits, null)).toEqual(['7', '30'])
    expect(supportedPeriodPresets({ ...limits, maxDays: 365 }, null))
      .toEqual(['7', '30', '90', '180', '365'])
    expect(supportedPeriodPresets({ ...limits, maxDays: 3661 }, null))
      .toEqual(['7', '30', '90', '180', '365'])
    expect(supportedPeriodPresets({ ...limits, minDays: 8 }, null)).toEqual(['30'])
  })

  it('offers all only with a valid complete range within every configured limit', () => {
    expect(supportedPeriodPresets(limits, { start: '2024-02-29', end: '2024-03-30' }))
      .toEqual(['7', '30', 'all'])
    expect(supportedPeriodPresets(limits, { start: '2024-02-28', end: '2024-03-30' }))
      .toEqual(['7', '30'])
    expect(supportedPeriodPresets({ ...limits, maxDate: '2024-03-29' },
      { start: '2024-02-29', end: '2024-03-30' })).toEqual(['7', '30'])
  })

  it('honors minimum days and an optional maximum date without imposing today universally', () => {
    expect(validPeriodRange({ start: '2024-03-30', end: '2024-03-30' }, { ...limits, minDays: 2 }))
      .toBe(false)
    expect(validPeriodRange({ start: '2024-03-29', end: '2024-03-30' }, { ...limits, minDays: 2 }))
      .toBe(true)
    expect(validPeriodRange({ start: '2024-03-30', end: '2024-03-31' },
      { ...limits, maxDate: '2024-03-30' })).toBe(false)
    expect(validPeriodRange({ start: '2024-03-30', end: '2024-03-31' }, limits)).toBe(true)
  })

  it.each(['year', '60'])('keeps old explicit %s dates as custom instead of offering obsolete presets', (period) => {
    expect(periodFromQuery({ period, start: '2026-01-01', end: '2026-03-30' },
      { ...limits, maxDays: 3661 }, '2026-03-30', null)).toEqual({
      period: 'custom', start: '2026-01-01', end: '2026-03-30',
    })
  })

  it('falls back to 30 days when all has no valid full reader range', () => {
    expect(periodFromQuery({ period: 'all' }, limits, '2026-03-30', null)).toEqual({
      period: '30', start: '2026-03-01', end: '2026-03-30',
    })
    expect(periodFromQuery({ period: 'all' }, limits, '2024-03-30',
      { start: '2024-02-28', end: '2024-03-30' })).toEqual({
      period: '30', start: '2024-03-01', end: '2024-03-30',
    })
  })

  it('preserves the exact supplied all range instead of reconstructing it from today or maxDays', () => {
    expect(periodFromQuery({ period: 'all' }, limits, '2024-03-30',
      { start: '2024-02-29', end: '2024-03-29' })).toEqual({
      period: 'all', start: '2024-02-29', end: '2024-03-29',
    })
  })

  it('recognizes inclusive rolling dates but leaves explicit non-preset dates custom', () => {
    expect(periodFromQuery({ start: '2024-03-01', end: '2024-03-30' },
      limits, '2024-03-30', null)).toEqual({
      period: '30', start: '2024-03-01', end: '2024-03-30',
    })
    expect(periodFromQuery({ start: '2024-02-29', end: '2024-03-30' },
      limits, '2024-03-30', null)).toEqual({
      period: 'custom', start: '2024-02-29', end: '2024-03-30',
    })
  })

  it.each([
    { start: '2024-03-01', end: '2024-03-30' },
    { period: '30', start: '2024-03-01', end: '2024-03-30' },
  ])('preserves a historical explicit 30-day interval as custom rather than rolling it to today: $period', (query) => {
    expect(periodFromQuery(query, { ...limits, maxDate: '2026-10-01' }, '2026-10-01', null))
      .toEqual({ period: 'custom', start: '2024-03-01', end: '2024-03-30' })
  })

  it.each([
    { start: '2026-09-02', end: '2026-10-01' },
    { period: '30', start: '2026-09-02', end: '2026-10-01' },
  ])('keeps an explicit 30-day interval ending today as a rolling preset: $period', (query) => {
    expect(periodFromQuery(query, limits, '2026-10-01', null))
      .toEqual({ period: '30', start: '2026-09-02', end: '2026-10-01' })
  })

  it('accepts a supported preset-only query as 90 inclusive days ending today', () => {
    expect(periodFromQuery({ period: '90' }, { ...limits, maxDays: 365 }, '2026-10-01', null))
      .toEqual({ period: '90', start: '2026-07-04', end: '2026-10-01' })
  })

  it.each([
    { period: '90' as const, start: '2026-07-04' },
    { period: '180' as const, start: '2026-04-05' },
    { period: '365' as const, start: '2025-10-02' },
  ])('preserves rolling Analysis preset $period through its inclusive range', ({ period, start }) => {
    const analysisLimits = { minDays: 1, maxDays: 365, customRange: true, maxDate: '2026-10-01' }
    expect(periodFromQuery({ period }, analysisLimits, '2026-10-01', null))
      .toEqual({ period, start, end: '2026-10-01' })
  })

  it('accepts an inclusive 365-day leap-year custom range but rejects 366 days', () => {
    const analysisLimits = { minDays: 1, maxDays: 365, customRange: true, maxDate: '2024-03-30' }
    expect(validPeriodRange({ start: '2023-04-01', end: '2024-03-30' }, analysisLimits)).toBe(true)
    expect(validPeriodRange({ start: '2023-03-31', end: '2024-03-30' }, analysisLimits)).toBe(false)
    expect(periodFromQuery(
      { period: 'custom', start: '2023-03-31', end: '2024-03-30' },
      analysisLimits, '2024-03-30', null,
    )).toEqual({ period: '30', start: '2024-03-01', end: '2024-03-30' })
  })

  it.each([
    { label: 'invalid calendar date', query: { period: '365', start: '2026-02-30', end: '2026-10-01' } },
    { label: 'reversed bounds', query: { period: '365', start: '2026-10-02', end: '2026-10-01' } },
    { label: 'missing end', query: { period: '365', start: '2025-10-02' } },
    { label: 'array-valued bounds', query: { period: '365', start: ['2025-10-02'], end: '2026-10-01' } },
    { label: 'unknown rolling preset', query: { period: 'future' } },
    { label: 'unsupported All-data selection', query: { period: 'all' } },
  ])('uses the common 30-day fallback for invalid rolling Analysis selection: $label', ({ query }) => {
    const analysisLimits = { minDays: 1, maxDays: 365, customRange: true, maxDate: '2026-10-01' }
    expect(periodFromQuery(query, analysisLimits, '2026-10-01', null))
      .toEqual({ period: '30', start: '2026-09-02', end: '2026-10-01' })
  })

  it.each([
    { label: 'malformed start without end', query: { period: '90', start: 'not-a-date' } },
    { label: 'malformed end without start', query: { period: '90', end: 'not-a-date' } },
    { label: 'missing start', query: { period: '90', end: '2026-10-01' } },
    { label: 'missing end', query: { period: '90', start: '2026-07-04' } },
    { label: 'array-valued start', query: { period: '90', start: ['2026-07-04', '2026-07-05'], end: '2026-10-01' } },
    { label: 'array-valued end', query: { period: '90', start: '2026-07-04', end: ['2026-09-30', '2026-10-01'] } },
  ])('rejects a supported 90-day query with $label instead of ignoring explicit bounds', ({ query }) => {
    expect(periodFromQuery(query, { ...limits, maxDays: 365 }, '2026-10-01', null))
      .toEqual({ period: '30', start: '2026-09-02', end: '2026-10-01' })
  })

  it.each([
    { period: 'custom', start: '2024-02-28', end: '2024-03-30' },
    { period: 'custom', start: '2024-02-30', end: '2024-03-01' },
    { period: 'custom', start: '2024-03-02', end: '2024-03-01' },
    { period: 'custom', start: '2024-03-30', end: '2024-04-01' },
    { period: '365' },
  ])('rejects unsupported or invalid query selection $period / $start / $end', (query) => {
    expect(periodFromQuery(query, { ...limits, maxDate: '2024-03-30' }, '2024-03-30', null))
      .toEqual({ period: '30', start: '2024-03-01', end: '2024-03-30' })
  })

  it.each([
    { period: 'custom', start: '2024-02-29', end: '2024-03-30' },
    { start: '2024-02-29', end: '2024-03-30' },
    { period: 'year', start: '2024-02-29', end: '2024-03-30' },
  ])('does not activate a custom query when the reader forbids custom ranges: $period', (query) => {
    expect(periodFromQuery(query, { ...limits, customRange: false }, '2024-03-30', null))
      .toEqual({ period: '30', start: '2024-03-01', end: '2024-03-30' })
  })

  it('still accepts a supported preset when custom ranges are forbidden', () => {
    expect(periodFromQuery({ period: '7', start: '2024-03-24', end: '2024-03-30' },
      { ...limits, customRange: false }, '2024-03-30', null)).toEqual({
      period: '7', start: '2024-03-24', end: '2024-03-30',
    })
  })

  it('requires every reader and validates the earliest union against every limit', () => {
    expect(commonAllDataRange([], '2024-03-30')).toBeNull()
    expect(commonAllDataRange([
      { capability: yes, limits },
      { capability: undefined, limits: { ...limits, maxDays: 3661 } },
    ], '2024-03-30')).toBeNull()
    expect(commonAllDataRange([
      { capability: yes, limits },
      { capability: no, limits },
    ], '2024-03-30')).toBeNull()
    expect(commonAllDataRange([
      { capability: yes, limits },
      { capability: { ...yes, all_data_start_date: '2024-02-28' }, limits: { ...limits, maxDays: 3661 } },
    ], '2024-03-30')).toBeNull()
  })

  it('keeps every reader complete by using the earliest valid union boundary, independent of order', () => {
    const readers = [
      { capability: yes, limits },
      { capability: { ...yes, all_data_start_date: '2024-03-01' }, limits: { ...limits, maxDays: 365 } },
    ]
    expect(commonAllDataRange(readers, '2024-03-30'))
      .toEqual({ start: '2024-02-29', end: '2024-03-30' })
    expect(commonAllDataRange([...readers].reverse(), '2024-03-30'))
      .toEqual({ start: '2024-02-29', end: '2024-03-30' })
  })

  it.each([
    { maxDays: 365, validStart: '2025-04-01', invalidStart: '2025-03-31' },
    { maxDays: 3661, validStart: '2016-03-23', invalidStart: '2016-03-22' },
  ])('honors exactly $maxDays inclusive days without clipping an older All boundary', ({ maxDays, validStart, invalidStart }) => {
    const readerLimits = { ...limits, maxDays }
    expect(resolveAllDataRange({ supports_all_data: true, all_data_start_date: validStart }, '2026-03-31', readerLimits))
      .toEqual({ start: validStart, end: '2026-03-31' })
    expect(resolveAllDataRange({ supports_all_data: true, all_data_start_date: invalidStart }, '2026-03-31', readerLimits))
      .toBeNull()
  })

  it('keeps response-owned All usable even when free custom ranges are forbidden', () => {
    const readerLimits = { ...limits, customRange: false }
    const allRange = commonAllDataRange([
      { limits: readerLimits, capability: { supports_all_data: true, all_data_start_date: '2026-03-01' } },
    ], '2026-03-31')
    expect(allRange).toEqual({ start: '2026-03-01', end: '2026-03-31' })
    expect(periodFromQuery({ period: 'all' }, readerLimits, '2026-03-31', allRange))
      .toEqual({ period: 'all', start: '2026-03-01', end: '2026-03-31' })
  })
})
