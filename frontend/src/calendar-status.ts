import { i18n } from './i18n'

const classificationKeys: Readonly<Record<string, string>> = {
  under_budget: 'calendar.withinBudget',
  over_budget: 'calendar.overBudget',
  above_maintenance: 'calendar.overMaintenance',
  probably_incomplete: 'daily.withoutCalories',
  no_target: 'calendar.noTarget',
  no_data: 'calendar.noData',
}

export function calendarClassificationClass(value: string | undefined): string | undefined {
  return value && Object.hasOwn(classificationKeys, value) ? `calendar-status--${value}` : undefined
}

export function calendarClassificationLabel(value: string | undefined): string {
  return i18n.global.t(classificationKeys[value ?? 'no_data'] ?? 'calendar.noData')
}
