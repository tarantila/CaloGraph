const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u
const GERMAN_DATE = /^(\d{2})\.(\d{2})\.(\d{4})$/u

function isCalendarDate(year: number, month: number, day: number): boolean {
  const candidate = new Date(Date.UTC(year, month - 1, day))
  return (
    candidate.getUTCFullYear() === year
    && candidate.getUTCMonth() === month - 1
    && candidate.getUTCDate() === day
  )
}

export function formatGermanDate(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts) return value
  return isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))
    ? `${parts[3]}.${parts[2]}.${parts[1]}`
    : value
}

export function formatGermanDayMonth(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts) return value
  return isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))
    ? `${parts[3]}.${parts[2]}.`
    : value
}

export function parseGermanDate(value: string): string | null {
  const parts = value.match(GERMAN_DATE)
  if (!parts) return null
  const [, day, month, year] = parts
  return isCalendarDate(Number(year), Number(month), Number(day))
    ? `${year}-${month}-${day}`
    : null
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
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) {
    return null
  }
  return new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))).getUTCDay()
}

export function formatGermanWeekday(value: string): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) {
    return value
  }
  return new Intl.DateTimeFormat('de-DE', { weekday: 'short', timeZone: 'UTC' }).format(
    new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]))),
  )
}

export function shiftIsoDate(value: string, days: number): string {
  const parts = value.match(ISO_DATE)
  if (!parts || !isCalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]))) {
    return value
  }
  const shifted = new Date(Date.UTC(Number(parts[1]), Number(parts[2]) - 1, Number(parts[3]) + days))
  return shifted.toISOString().slice(0, 10)
}

export function formatGermanInstantDate(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).format(parsed)
}

export function formatGermanDateTime(value: string): string {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('de-DE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}
