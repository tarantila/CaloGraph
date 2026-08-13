import { intlLocale } from './i18n'

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u
const DISPLAY_DATE = /^(\d{2})[./](\d{2})[./](\d{4})$/u

function isCalendarDate(year: number, month: number, day: number): boolean {
  const candidate = new Date(Date.UTC(year, month - 1, day))
  return (
    candidate.getUTCFullYear() === year
    && candidate.getUTCMonth() === month - 1
    && candidate.getUTCDate() === day
  )
}

export function formatDate(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))))
}

export function formatDayMonth(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3])))
  )
}

export function parseDate(value: string): string | null {
  const parts = value.match(DISPLAY_DATE)
  if (!parts) return null
  const [, first, second, year] = parts
  const firstNumber = Number(first)
  const secondNumber = Number(second)
  const day = firstNumber
  const month = secondNumber
  const dayValue = `${day}`.padStart(2, '0')
  const monthValue = `${month}`.padStart(2, '0')
  return isCalendarDate(Number(year), month, day) ? `${year}-${monthValue}-${dayValue}` : null
}

export function isoDateInTimeZone(timeZone: string, value: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value)
  const calendar = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${calendar.year}-${calendar.month}-${calendar.day}`
}

export function isoWeekday(value: string): number | null {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return null
  return new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))).getUTCDay()
}

export function formatWeekday(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))),
  )
}

export function shiftIsoDate(value: string, days: number): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  const shifted = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]) + days))
  return shifted.toISOString().slice(0, 10)
}

export function formatInstantDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(parsed)
}

export function formatDateTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}

// Compatibility aliases preserve the historical German component contract.
export function formatGermanDate(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))))
}

export function formatGermanDayMonth(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), { day: '2-digit', month: '2-digit', timeZone: 'UTC' }).format(
    new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))),
  )
}

export const parseGermanDate = parseDate

export function formatGermanWeekday(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) return value
  return new Intl.DateTimeFormat(intlLocale(), { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))),
  )
}

export function formatGermanInstantDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(intlLocale(), { day: '2-digit', month: '2-digit', year: 'numeric' }).format(parsed)
}

export function formatGermanDateTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat(intlLocale(), {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}
