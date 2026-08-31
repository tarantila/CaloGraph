import { createI18n } from 'vue-i18n'

import en from './locales/en'
import de from './locales/de'

export const SUPPORTED_LOCALES = ['de', 'en'] as const
export type SupportedLocale = (typeof SUPPORTED_LOCALES)[number]
export const DEFAULT_LOCALE: SupportedLocale = 'de'
export const PUBLIC_LOCALE: SupportedLocale = DEFAULT_LOCALE
export const INTL_LOCALES: Record<SupportedLocale, string> = {
  de: 'de-DE',
  en: 'en-GB',
}

export function isSupportedLocale(value: unknown): value is SupportedLocale {
  return value === 'de' || value === 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: PUBLIC_LOCALE,
  fallbackLocale: DEFAULT_LOCALE,
  messages: { de, en },
  missingWarn: import.meta.env.DEV,
  fallbackWarn: import.meta.env.DEV,
})

export function setLocale(value: unknown): SupportedLocale {
  const locale: SupportedLocale = isSupportedLocale(value) ? value : DEFAULT_LOCALE
  i18n.global.locale.value = locale
  if (typeof document !== 'undefined') document.documentElement.lang = locale
  return locale
}

export function applyUserLocale(value: unknown): SupportedLocale {
  return setLocale(value)
}

export function intlLocale(): string {
  return INTL_LOCALES[i18n.global.locale.value as SupportedLocale] ?? INTL_LOCALES.de
}

export function formatNumber(value: number, options: Intl.NumberFormatOptions = {}): string {
  return new Intl.NumberFormat(intlLocale(), options).format(value)
}

export function formatDate(value: string | Date, options: Intl.DateTimeFormatOptions = {}): string {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat(intlLocale(), options).format(date)
}

export function createNumberFormatter(options: Intl.NumberFormatOptions = {}): Pick<Intl.NumberFormat, 'format'> {
  return { format: (value: number) => formatNumber(value, options) }
}

export function createDateFormatter(options: Intl.DateTimeFormatOptions = {}): Pick<Intl.DateTimeFormat, 'format'> {
  return { format: (value: number | Date) => formatDate(value instanceof Date ? value : new Date(value), options) }
}

setLocale(PUBLIC_LOCALE)
