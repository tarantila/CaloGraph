import analysisPresentationMap from './analysis-presentation.json'
import { i18n, type SupportedLocale } from './i18n'
import type { AnalysisCategoryKey } from './types'

export interface AnalysisPresentation {
  label: Record<SupportedLocale, string>
  description: Record<SupportedLocale, string>
  icon: string
  route: 'weight' | 'active-energy' | 'micronutrient' | 'catalog'
}

export interface LocalizedAnalysisPresentation {
  label: string
  description: string
  icon: string
  route: AnalysisPresentation['route']
}

export const analysisPresentation = analysisPresentationMap as Record<string, AnalysisPresentation>

export function localizedAnalysisPresentation(key: string): LocalizedAnalysisPresentation | undefined {
  const presentation = analysisPresentation[key]
  if (!presentation) return undefined
  const locale = i18n.global.locale.value as SupportedLocale
  return {
    label: presentation.label[locale],
    description: presentation.description[locale],
    icon: presentation.icon,
    route: presentation.route,
  }
}
export const analysisCategories: readonly { key: AnalysisCategoryKey; labelKey: string }[] = [
  { key: 'body', labelKey: 'analysisV2.categories.body' },
  { key: 'activity', labelKey: 'analysisV2.categories.activity' },
  { key: 'nutrition', labelKey: 'analysisV2.categories.nutrition' },
  { key: 'micronutrients', labelKey: 'analysisV2.categories.micronutrients' },
  { key: 'fluid', labelKey: 'analysisV2.categories.fluid' },
]

export type AnalysisHubPeriod = '7' | '30'

export function parseAnalysisHubPeriod(value: unknown): AnalysisHubPeriod {
  return value === '7' ? '7' : '30'
}
export function analysisHubQuery(value: unknown): { period: AnalysisHubPeriod } {
  return { period: parseAnalysisHubPeriod(value) }
}

export function analysisRangeForDays(today: string, days: number): { start: string; end: string } {
  const date = new Date(`${today}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() - days + 1)
  return { start: date.toISOString().slice(0, 10), end: today }
}

export function analysisHref(key: string): string {
  const presentation = analysisPresentation[key]
  if (!presentation) return '/analyse/katalog'
  switch (presentation.route) {
    case 'weight': return '/analyse/koerper/gewicht'
    case 'active-energy': return '/analyse/aktivitaet/aktive-energie'
    case 'micronutrient': return `/analyse/mikronaehrstoffe/${encodeURIComponent(key)}`
    default: return '/analyse/katalog'
  }
}

export function analysisIconSymbol(icon: string): string {
  switch (icon) {
    case 'scale': return '◉'
    case 'fire': return '↗'
    case 'leaf': return '⌁'
    case 'drop': return '◯'
    case 'activity': return '↗'
    case 'nutrition': return '⌁'
    default: return '⊙'
  }
}
