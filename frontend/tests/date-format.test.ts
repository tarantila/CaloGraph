import { beforeEach, describe, expect, it } from 'vitest'

import {
  formatGermanDate,
  formatGermanDayMonth,
  formatGermanWeekday,
  isoDateInTimeZone,
  isoWeekday,
  parseGermanDate,
  shiftIsoDate,
} from '../src/date-format'
import { DEFAULT_LOCALE, setLocale } from '../src/i18n'

beforeEach(() => {
  setLocale(DEFAULT_LOCALE)
})

describe('German calendar date contract', () => {
  it('formats ISO calendar dates independently of browser locale', () => {
    expect(formatGermanDate('2026-08-11')).toBe('11.08.2026')
    expect(formatGermanDayMonth('2026-07-03')).toBe('03.07.')
    expect(formatGermanWeekday('2026-08-11')).toBe('Di')
    expect(isoWeekday('2026-08-11')).toBe(2)
    const instant = new Date('2026-08-11T00:30:00Z')
    expect(isoDateInTimeZone('UTC', instant)).toBe('2026-08-11')
    expect(isoDateInTimeZone('America/Los_Angeles', instant)).toBe('2026-08-10')
  })

  it('parses valid German dates and rejects impossible dates', () => {
    expect(parseGermanDate('02.01.2026')).toBe('2026-01-02')
    expect(parseGermanDate('29.02.2024')).toBe('2024-02-29')
    expect(parseGermanDate('29.02.2025')).toBeNull()
    expect(parseGermanDate('31.04.2026')).toBeNull()
    expect(parseGermanDate('2026-01-02')).toBeNull()
  })

  it('shifts ISO dates across month and leap-year boundaries', () => {
    expect(shiftIsoDate('2026-01-30', 6)).toBe('2026-02-05')
    expect(shiftIsoDate('2024-02-28', 1)).toBe('2024-02-29')
  })
})
