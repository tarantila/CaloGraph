import { shiftIsoDate } from './date-format'
import type { ReaderPeriodCapabilities } from './types'

export type PeriodPreset = '7' | '30' | '90' | '180' | '365' | '3661' | 'all'
export type PeriodSelection = PeriodPreset | 'custom'
export interface PeriodRange { start: string; end: string }
export interface AppliedPeriod extends PeriodRange { period: PeriodSelection }
export interface PeriodLimits {
  minDays: number
  maxDays: number
  customRange: boolean
  maxDate?: string
}
export const rollingPeriods = ['7', '30', '90', '180', '365'] as const

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

export function analysisRangeWithinDays(start: unknown, end: unknown, maxDays: number): boolean {
  if (!validAnalyticsRange(start, end) || !Number.isInteger(maxDays) || maxDays < 1) return false
  const startOrdinal = Date.parse(`${start as string}T00:00:00Z`) / 86_400_000
  const endOrdinal = Date.parse(`${end as string}T00:00:00Z`) / 86_400_000
  return endOrdinal - startOrdinal + 1 <= maxDays
}

export function rangeForDays(end: string, days: number): PeriodRange {
  return { start: shiftIsoDate(end, -(days - 1)), end }
}

export function validPeriodRange(range: PeriodRange, limits: PeriodLimits): boolean {
  if (!analysisRangeWithinDays(range.start, range.end, limits.maxDays)) return false
  const days = (Date.parse(`${range.end}T00:00:00Z`) - Date.parse(`${range.start}T00:00:00Z`)) / 86_400_000 + 1
  return days >= limits.minDays && (!limits.maxDate || range.end <= limits.maxDate)
}

export function resolveAllDataRange(
  capability: ReaderPeriodCapabilities | null | undefined,
  resolvedEnd: string,
  limits: PeriodLimits,
): PeriodRange | null {
  if (capability?.supports_all_data !== true || !capability.all_data_start_date) return null
  const range = { start: capability.all_data_start_date, end: resolvedEnd }
  return validPeriodRange(range, limits) ? range : null
}

export function supportedPeriodPresets(
  limits: PeriodLimits,
  allRange: PeriodRange | null,
  additionalPresets: readonly PeriodPreset[] = [],
): PeriodPreset[] {
  const presets: PeriodPreset[] = rollingPeriods.filter((period) =>
    Number(period) >= limits.minDays && Number(period) <= limits.maxDays)
  for (const preset of additionalPresets) {
    if (preset === 'all' || rollingPeriods.includes(preset as typeof rollingPeriods[number])) continue
    const days = Number(preset)
    if (days >= limits.minDays && days <= limits.maxDays) presets.push(preset)
  }
  if (allRange && validPeriodRange(allRange, limits)) presets.push('all')
  return presets
}

export function periodFromQuery(
  query: { period?: unknown; start?: unknown; end?: unknown },
  limits: PeriodLimits,
  today: string,
  allRange: PeriodRange | null,
  additionalPresets: readonly PeriodPreset[] = [],
): AppliedPeriod {
  const fallback: AppliedPeriod = { ...rangeForDays(today, 30), period: '30' }
  const presets: readonly PeriodPreset[] = [...rollingPeriods, ...additionalPresets]
  if (query.period === 'all') return allRange && validPeriodRange(allRange, limits)
    ? { ...allRange, period: 'all' } : fallback
  const provided = typeof query.start === 'string' && typeof query.end === 'string'
    ? { start: query.start, end: query.end } : null
  if (provided && validPeriodRange(provided, limits)) {
    if (query.period === 'custom' && !limits.customRange) return fallback
    const preset = presets.find((period) => (
      (query.period === undefined || query.period === period)
      && provided.end === today
      && Number(period) >= limits.minDays && Number(period) <= limits.maxDays
      && provided.start === rangeForDays(provided.end, Number(period)).start
    ))
    if (!preset && !limits.customRange) return fallback
    return { ...provided, period: preset ?? 'custom' }
  }
  if ('start' in query || 'end' in query || query.period === 'custom') return fallback
  const preset = presets.find((period) => query.period === period
    && Number(period) >= limits.minDays && Number(period) <= limits.maxDays)
  return preset ? { ...rangeForDays(today, Number(preset)), period: preset } : fallback
}

export function commonAllDataRange(
  readers: readonly {
    capability: ReaderPeriodCapabilities | null | undefined
    limits: PeriodLimits
  }[],
  resolvedEnd: string,
): PeriodRange | null {
  if (!readers.length) return null
  const ranges = readers.map(({ capability, limits }) => resolveAllDataRange(capability, resolvedEnd, limits))
  if (ranges.some((range) => !range)) return null
  const start = ranges.reduce((first, range) => range!.start < first ? range!.start : first, resolvedEnd)
  const combined = { start, end: resolvedEnd }
  return readers.every(({ limits }) => validPeriodRange(combined, limits)) ? combined : null
}
