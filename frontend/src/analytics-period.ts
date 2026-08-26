import { shiftIsoDate } from './date-format'

export type AnalyticsCompactPreset = '7' | '30' | '60' | '90' | '180' | 'year' | 'all' | 'custom'

export function parseAnalyticsCompactPreset(
  value: unknown,
  options: readonly AnalyticsCompactPreset[],
): AnalyticsCompactPreset | undefined {
  if (value === 'custom') return 'custom'
  return typeof value === 'string' && options.includes(value as AnalyticsCompactPreset)
    ? value as AnalyticsCompactPreset
    : undefined
}

export function validAnalyticsRange(start: unknown, end: unknown): boolean {
  if (typeof start !== 'string' || typeof end !== 'string') return false
  if (!/^\d{4}-\d{2}-\d{2}$/u.test(start) || !/^\d{4}-\d{2}-\d{2}$/u.test(end)) return false
  const startDate = new Date(`${start}T00:00:00Z`)
  const endDate = new Date(`${end}T00:00:00Z`)
  return !Number.isNaN(startDate.getTime())
    && !Number.isNaN(endDate.getTime())
    && startDate.toISOString().slice(0, 10) === start
    && endDate.toISOString().slice(0, 10) === end
    && start <= end
}

export function analyticsPresetMatchesRange(
  value: AnalyticsCompactPreset | undefined,
  start: unknown,
  end: unknown,
): boolean {
  if (!value || !validAnalyticsRange(start, end)) return false
  if (value === 'custom' || value === 'all') return true
  const endDate = end as string
  const expectedStart = value === 'year'
    ? `${endDate.slice(0, 4)}-01-01`
    : shiftIsoDate(endDate, -(Number(value) - 1))
  return start === expectedStart
}

export function inferDesktopPreset(start: string, end: string): string {
  if (start === shiftIsoDate(end, -29)) return '30'
  if (start === shiftIsoDate(end, -89)) return '90'
  if (start === shiftIsoDate(end, -179)) return '180'
  if (start === `${end.slice(0, 4)}-01-01`) return 'year'
  return 'custom'
}
