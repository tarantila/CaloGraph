<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'

import {
  AnalysisIcon, analysisHref, analysisHubQuery, canonicalAnalysisCardId,
  type LocalizedAnalysisPresentation,
} from '../analysis-v2'
import type { PeriodSelection } from '../analytics-period'
import { createNumberFormatter, i18n } from '../i18n'
import type { AnalysisMicronutrientResponse, AnalysisRegistryEntry } from '../types'

const t = i18n.global.t.bind(i18n.global)
const number = createNumberFormatter({ maximumFractionDigits: 1 })
const route = useRoute()

interface CatalogEntry {
  entry: AnalysisRegistryEntry
  presentation: LocalizedAnalysisPresentation
}

const props = withDefaults(defineProps<{
  entries: readonly CatalogEntry[]
  selectedKeys: readonly string[]
  period: PeriodSelection
  saving: boolean
  periodRange: { start: string; end: string }
  periodDays: number
  micronutrients?: AnalysisMicronutrientResponse | null
  hubPresentation?: boolean
  limit?: number
}>(), { limit: Number.POSITIVE_INFINITY, micronutrients: null, hubPresentation: false })

const emit = defineEmits<{ add: [key: string]; remove: [key: string] }>()
const shownEntries = computed(() => props.entries.slice(0, props.limit))
function isSelected(entry: AnalysisRegistryEntry): boolean {
  return props.selectedKeys.includes(canonicalAnalysisCardId(entry.analysis_key))
}

const categoryPath: Record<AnalysisRegistryEntry['category_key'], string> = {
  body: '/analyse/koerper',
  activity: '/analyse/aktivitaet',
  nutrition: '/analyse/ernaehrung',
  micronutrients: '/analyse/mikronaehrstoffe',
  fluid: '/analyse',
}

function entryPath({ entry, presentation }: CatalogEntry): string {
  return presentation.route === 'catalog'
    ? categoryPath[entry.category_key]
    : analysisHref(entry.analysis_key)
}

function entryQuery() {
  return {
    ...route.query, ...analysisHubQuery(props.period, props.periodRange),
    start: props.period === 'custom' ? props.periodRange.start : undefined,
    end: props.period === 'custom' ? props.periodRange.end : undefined,
  }
}

function iconRole(entry: AnalysisRegistryEntry): string {
  if (entry.analysis_key === 'weight_kg') return 'violet'
  if (entry.analysis_key === 'active_energy_kcal' || entry.analysis_key === 'fat_g') return 'amber'
  if (entry.category_key === 'micronutrients') return 'emerald'
  if (entry.analysis_key === 'protein_g') return 'teal'
  if (entry.analysis_key === 'carbohydrates_g') return 'blue'
  if (entry.status === 'coming_soon') return 'slate'
  return 'slate'
}
type CoverageStatus = { text: string; status?: 'complete' | 'incomplete' }

function hubMicronutrientDataBasis(days: number): string {
  if (days === 0) return t('analysisV2.hubNoDataPeriod')
  const key = days === 1 ? 'analysisV2.hubMicronutrientDataDay' : 'analysisV2.hubMicronutrientDataDays'
  return t(key, { count: number.format(days) })
}

const micronutrientCoverage = computed(() => {
  const byKey = new Map<string, CoverageStatus>()
  const metrics = props.micronutrients?.metrics ?? []
  for (const { entry } of shownEntries.value) {
    if (entry.category_key !== 'micronutrients') continue
    const metric = metrics.find((item) => item.metric_key === entry.analysis_key)
    if (!metric) continue
    byKey.set(entry.analysis_key, props.hubPresentation
      ? { text: hubMicronutrientDataBasis(metric.days_with_data) }
      : {
        text: metric.days_with_data === 0
          ? t('analysisV2.noDataPeriod')
          : t('analysisV2.daysCoverage', {
            count: number.format(metric.days_with_data),
            total: number.format(props.periodDays),
          }),
        status: metric.days_with_data === props.periodDays ? 'complete' : 'incomplete',
      })
  }
  return byKey
})
</script>

<template>
  <div class="analysis-catalog-grid">
    <article
      v-for="({ entry, presentation }) in shownEntries"
      :key="entry.analysis_key"
      :data-analysis-key="entry.analysis_key"
      :data-analysis-catalog-key="entry.analysis_key"
      class="analysis-catalog-entry"
      :aria-disabled="entry.status === 'coming_soon' ? 'true' : undefined"
    >
      <span class="analysis-catalog-icon" :class="`analysis-catalog-icon--${iconRole(entry)}`" aria-hidden="true">
        <AnalysisIcon :icon="presentation.icon" />
      </span>
      <div class="analysis-catalog-copy">
        <h3 class="analysis-catalog-title">
          <RouterLink
            v-if="entry.status === 'available' && entry.detail_capable"
            :to="{ path: entryPath({ entry, presentation }), query: entryQuery(), hash: entry.category_key === 'fluid' ? '#analysis-category-fluid' : route.hash }"
          >
            {{ presentation.label }}
          </RouterLink>
          <span v-else>{{ presentation.label }}</span>
        </h3>
        <span v-if="entry.status === 'coming_soon'" class="analysis-catalog-status">
          {{ t('analysisV2.comingSoon') }}
        </span>
        <span v-else-if="isSelected(entry)" class="analysis-catalog-selected" :data-analysis-selected="canonicalAnalysisCardId(entry.analysis_key)" role="status">
          <span aria-hidden="true">✓</span> {{ t('analysisV2.inMyAnalyses') }}
        </span>
        <span
          v-else-if="entry.category_key === 'micronutrients' && micronutrientCoverage.has(entry.analysis_key)"
          class="analysis-catalog-status"
          :class="micronutrientCoverage.get(entry.analysis_key)?.status
            ? `analysis-catalog-status--${micronutrientCoverage.get(entry.analysis_key)?.status}`
            : undefined"
          :data-coverage-status="micronutrientCoverage.get(entry.analysis_key)?.status"
        >
          <span
            v-if="micronutrientCoverage.get(entry.analysis_key)?.status"
            class="analysis-catalog-status-icon"
            aria-hidden="true"
          >
            {{ micronutrientCoverage.get(entry.analysis_key)?.status === 'complete' ? '✓' : '!' }}
          </span>
          {{ micronutrientCoverage.get(entry.analysis_key)?.text }}
        </span>
      </div>
      <button
        v-if="entry.status === 'available' && entry.card_capable && !isSelected(entry)"
        class="button secondary analysis-catalog-action"
        type="button"
        :data-analysis-add="entry.analysis_key"
        :aria-label="t('analysisV2.addCard', { label: presentation.label })"
        :disabled="saving"
        @click="emit('add', canonicalAnalysisCardId(entry.analysis_key))"
      >
        <span aria-hidden="true">+</span> {{ t('analysisV2.add') }}
      </button>
      <button
        v-else-if="isSelected(entry)"
        class="button secondary analysis-catalog-action"
        type="button"
        :aria-label="t('analysisV2.removeCard', { label: presentation.label })"
        :disabled="saving"
        @click="emit('remove', canonicalAnalysisCardId(entry.analysis_key))"
      >
        <span aria-hidden="true">−</span> {{ t('analysisV2.remove') }}
      </button>
    </article>
  </div>
</template>

<style scoped>
.analysis-catalog-grid {
  display: grid;
  grid-template-columns: 1fr;
  min-width: 0;
}
.analysis-catalog-entry {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) auto;
  align-items: center;
  gap: .65rem;
  min-width: 0;
  padding: .65rem .1rem;
  border-bottom: 1px solid var(--border);
  transition: background-color .16s ease;
}
.analysis-catalog-entry:last-child { border-bottom: 0; }
.analysis-catalog-entry:hover { background: color-mix(in srgb, var(--accent) 5%, var(--surface)); }
.analysis-catalog-entry[aria-disabled="true"] { opacity: .72; }
.analysis-catalog-icon {
  display: grid;
  place-items: center;
  width: 2.15rem;
  height: 2.15rem;
  border-radius: .62rem;
  color: var(--chart-series-baseline);
  background: color-mix(in srgb, var(--chart-series-baseline) 14%, transparent);
}
.analysis-catalog-icon--violet { color: var(--chart-series-violet); background: color-mix(in srgb, var(--chart-series-violet) 14%, transparent); }
.analysis-catalog-icon--amber { color: var(--chart-series-amber); background: color-mix(in srgb, var(--chart-series-amber) 14%, transparent); }
.analysis-catalog-icon--emerald { color: var(--chart-series-emerald); background: color-mix(in srgb, var(--chart-series-emerald) 14%, transparent); }
.analysis-catalog-icon--teal { color: var(--chart-series-teal); background: color-mix(in srgb, var(--chart-series-teal) 14%, transparent); }
.analysis-catalog-icon--blue { color: var(--chart-series-blue); background: color-mix(in srgb, var(--chart-series-blue) 14%, transparent); }
.analysis-catalog-copy { display: grid; gap: .2rem; min-width: 0; }
.analysis-catalog-title { margin: 0; font-size: .92rem; font-weight: 650; overflow-wrap: anywhere; hyphens: auto; }
.analysis-catalog-title a { color: inherit; text-decoration: none; }
.analysis-catalog-title a:hover { color: var(--accent-hover); }
.analysis-catalog-status--complete { color: var(--success); }
.analysis-catalog-status--incomplete { color: var(--warning); }
.analysis-catalog-status-icon { margin-right: .25rem; font-weight: 750; }
.analysis-catalog-action { min-height: 2.75rem; padding: .42rem .6rem; white-space: nowrap; font-size: .78rem; }
.analysis-catalog-action > span { font-size: 1.05rem; }
@media (max-width: 440px) {
  .analysis-catalog-entry { grid-template-columns: 2rem minmax(0, 1fr) auto; gap: .5rem; }
  .analysis-catalog-icon { width: 2rem; height: 2rem; }
  .analysis-catalog-action { min-width: 2.75rem; padding: .42rem; }
  .analysis-catalog-action > span { margin-right: 0; }
}
</style>
