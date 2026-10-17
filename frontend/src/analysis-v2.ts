import {
  PhAtom, PhAvocado, PhBarbell, PhBowlFood, PhBread, PhCarrot, PhCube, PhDiamond, PhDrop, PhEgg, PhFire,
  PhFootprints, PhForkKnife, PhLeaf, PhLightning, PhPersonSimpleRun, PhScales,
} from '@phosphor-icons/vue'
import { defineComponent, h, type Component } from 'vue'

import analysisPresentationMap from './analysis-presentation.json'
import type { PeriodRange, PeriodSelection } from './analytics-period'
import { i18n, type SupportedLocale } from './i18n'
import type { AnalysisCategoryKey, AnalysisNutritionMetricKey } from './types'

export interface AnalysisPresentation {
  label: Record<SupportedLocale, string>
  description: Record<SupportedLocale, string>
  icon: string
  route: 'weight' | 'active-energy' | 'micronutrient' | 'nutrition' | 'catalog'
}

export interface LocalizedAnalysisPresentation {
  label: string
  description: string
  icon: string
  route: AnalysisPresentation['route']
}

export const analysisPresentation = analysisPresentationMap as Record<string, AnalysisPresentation>
const nutritionMetricIcons: Record<string, string> = {
  protein_g: 'protein',
  carbohydrates_g: 'carbohydrates',
  fat_g: 'fat',
  fiber_g: 'fiber',
  saturated_fat_g: 'fat',
}

export function localizedAnalysisPresentation(key: string): LocalizedAnalysisPresentation | undefined {
  const presentation = Object.hasOwn(analysisPresentation, key) ? analysisPresentation[key] : undefined
  if (!presentation) return undefined
  const locale = i18n.global.locale.value as SupportedLocale
  return {
    label: presentation.label[locale],
    description: presentation.description[locale],
    icon: Object.hasOwn(nutritionMetricIcons, key) ? nutritionMetricIcons[key]! : presentation.icon,
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

export const DEFAULT_ACTIVE_CARD_IDS: readonly string[] = [
  'weight',
  'nutrition',
  'micronutrients',
  'activity',
]

export function canonicalAnalysisCardId(key: string): string {
  if (key === 'weight_kg') return 'weight'
  if (key === 'active_energy_kcal') return 'activity'
  return key
}

export function addAnalysisPreference(keys: readonly string[], key: string): string[] {
  const canonicalKey = canonicalAnalysisCardId(key)
  return keys.includes(canonicalKey) ? [...keys] : [...keys, canonicalKey]
}

export function analysisHubQuery(
  period: PeriodSelection,
  range?: PeriodRange,
): { period: PeriodSelection; start?: string; end?: string } {
  return period === 'custom' && range
    ? { period, start: range.start, end: range.end }
    : { period }
}

export function analysisRangeDayCount(range: { start: string; end: string }): number {
  return Math.floor(
    (Date.parse(`${range.end}T00:00:00Z`) - Date.parse(`${range.start}T00:00:00Z`)) / 86_400_000,
  ) + 1
}

export const HUB_MINI_CHART_MIN_LINE_VALUES = 4
export const HUB_MINI_CHART_MIN_LINE_COVERAGE = 0.5
export const HUB_MINI_CHART_MAX_BAR_WIDTH = 5
export const HUB_MINI_CHART_BAR_WIDTH_RATIO = 0.65

export function hubMiniChartVariant(
  values: readonly (number | null)[],
  dates: readonly (string | null)[] = [],
): 'line' | 'bars' {
  let count = 0
  let firstIndex = -1
  let lastIndex = -1
  let firstTimestamp = Number.POSITIVE_INFINITY
  let lastTimestamp = Number.NEGATIVE_INFINITY
  let allValuesHaveDates = true

  for (let index = 0; index < values.length; index += 1) {
    const value = values[index]
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    count += 1
    if (firstIndex === -1) firstIndex = index
    lastIndex = index
    const date = dates[index]?.slice(0, 10)
    const timestamp = date ? Date.parse(`${date}T00:00:00Z`) : Number.NaN
    if (Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === date) {
      firstTimestamp = Math.min(firstTimestamp, timestamp)
      lastTimestamp = Math.max(lastTimestamp, timestamp)
    } else {
      allValuesHaveDates = false
    }
  }

  if (count < HUB_MINI_CHART_MIN_LINE_VALUES) return 'bars'
  const actualSpan = allValuesHaveDates
    ? Math.floor((lastTimestamp - firstTimestamp) / 86_400_000) + 1
    : lastIndex - firstIndex + 1
  return count / actualSpan >= HUB_MINI_CHART_MIN_LINE_COVERAGE ? 'line' : 'bars'
}

export const nutritionAnalysisKeys: readonly AnalysisNutritionMetricKey[] = [
  'dietary_energy_kcal', 'protein_g', 'carbohydrates_g', 'fat_g',
  'fiber_g', 'sugar_g', 'saturated_fat_g',
]

export function isNutritionAnalysisKey(key: string): key is AnalysisNutritionMetricKey {
  return nutritionAnalysisKeys.some((supported) => supported === key)
}

export function analysisHref(key: string): string {
  const presentation = Object.hasOwn(analysisPresentation, key) ? analysisPresentation[key] : undefined
  if (!presentation) return '/analyse/katalog'
  switch (presentation.route) {
    case 'weight': return '/analyse/koerper/gewicht'
    case 'active-energy': return '/analyse/aktivitaet/aktive-energie'
    case 'micronutrient': return `/analyse/mikronaehrstoffe/${encodeURIComponent(key)}`
    case 'nutrition': return isNutritionAnalysisKey(key)
      ? `/analyse/ernaehrung/${encodeURIComponent(key)}` : '/analyse/katalog'
    default: return '/analyse/katalog'
  }
}

const analysisIcons: Record<string, Component> = {
  scale: PhScales,
  nutrition: PhForkKnife,
  energy: PhLightning,
  protein: PhEgg,
  carbohydrates: PhBread,
  fat: PhAvocado,
  fiber: PhLeaf,
  bowl: PhBowlFood,
  cube: PhCube,
  leaf: PhLeaf,
  atom: PhAtom,
  carrot: PhCarrot,
  diamond: PhDiamond,
  fire: PhFire,
  activity: PhPersonSimpleRun,
  steps: PhFootprints,
  barbell: PhBarbell,
  drop: PhDrop,
}

export const AnalysisIcon = defineComponent({
  name: 'AnalysisIcon',
  props: {
    icon: { type: String, required: true },
  },
  setup(props) {
    return () => {
      const icon = Object.hasOwn(analysisIcons, props.icon) ? analysisIcons[props.icon]! : PhAtom
      return h(icon, {
        size: '1em',
        weight: 'bold',
        color: 'currentColor',
        'aria-hidden': 'true',
      })
    }
  },
})
