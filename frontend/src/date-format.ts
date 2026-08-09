const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u

export function formatGermanDate(value: string): string {
  const parts = value.match(ISO_DATE)
  return parts ? `${parts[3]}-${parts[2]}-${parts[1]}` : value
}

export function formatGermanDayMonth(value: string): string {
  const parts = value.match(ISO_DATE)
  return parts ? `${parts[3]}-${parts[2]}` : value
}
