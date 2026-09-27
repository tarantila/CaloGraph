<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import { analysisHref, analysisHubQuery, analysisIconSymbol, type AnalysisHubPeriod, type LocalizedAnalysisPresentation } from '../analysis-v2'
import { createNumberFormatter, i18n } from '../i18n'
import type { AnalysisMicronutrientResponse, AnalysisRegistryEntry } from '../types'

const t = i18n.global.t.bind(i18n.global)
const number = createNumberFormatter({ maximumFractionDigits: 1 })

interface CatalogEntry {
  entry: AnalysisRegistryEntry
  presentation: LocalizedAnalysisPresentation
}

const props = withDefaults(defineProps<{
  entries: readonly CatalogEntry[]
  selectedKeys: readonly string[]
  saving: boolean
  period: AnalysisHubPeriod
  micronutrients?: AnalysisMicronutrientResponse | null
  limit?: number
}>(), { limit: Number.POSITIVE_INFINITY, micronutrients: null })

const emit = defineEmits<{ add: [key: string] }>()
const shownEntries = computed(() => props.entries.slice(0, props.limit))

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

function micronutrientMetric(key: string) {
  return props.micronutrients?.metrics.find((metric) => metric.metric_key === key)
}

function micronutrientStatus(key: string): string | null {
  const metric = micronutrientMetric(key)
  if (!metric) return null
  if (metric.days_with_data === 0) return t('analysisV2.noDataPeriod')
  return t('analysisV2.daysCoverage', {
    count: number.format(metric.days_with_data),
    total: number.format(Number(props.period)),
  })
}
</script>

<template>
  <div class="analysis-catalog-grid">
    <article
      v-for="({ entry, presentation }) in shownEntries"
      :key="entry.analysis_key"
      class="card analysis-catalog-entry"
      :data-analysis-key="entry.analysis_key"
      :data-analysis-catalog-key="entry.analysis_key"
      :aria-disabled="entry.status === 'coming_soon' ? 'true' : undefined"
    >
      <span class="analysis-catalog-icon" aria-hidden="true">{{ analysisIconSymbol(presentation.icon) }}</span>
      <div class="analysis-catalog-copy">
        <h3 class="analysis-catalog-title">
          <RouterLink
            v-if="entry.status === 'available' && entry.detail_capable"
            :to="{ path: entryPath({ entry, presentation }), query: analysisHubQuery(period) }"
          >
            {{ presentation.label }}
          </RouterLink>
          <span v-else>{{ presentation.label }}</span>
        </h3>
        <span v-if="entry.status === 'coming_soon'" class="analysis-catalog-status">
          {{ t('analysisV2.comingSoon') }}
        </span>
        <span v-else-if="selectedKeys.includes(entry.analysis_key)" class="analysis-catalog-selected" :data-analysis-selected="entry.analysis_key" role="status">
          <span aria-hidden="true">✓</span> {{ t('analysisV2.inMyAnalyses') }}
        </span>
        <span
          v-else-if="entry.category_key === 'micronutrients' && micronutrientStatus(entry.analysis_key)"
          class="analysis-catalog-status"
        >
          {{ micronutrientStatus(entry.analysis_key) }}
        </span>
      </div>
      <button
        v-if="entry.status === 'available' && entry.card_capable && !selectedKeys.includes(entry.analysis_key)"
        class="button secondary analysis-catalog-action"
        type="button"
        :data-analysis-add="entry.analysis_key"
        :aria-label="t('analysisV2.addCard', { label: presentation.label })"
        :disabled="saving"
        @click="emit('add', entry.analysis_key)"
      >
        <span aria-hidden="true">+</span> {{ t('analysisV2.add') }}
      </button>
    </article>
  </div>
</template>

<style scoped>
.analysis-catalog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
  gap: .65rem;
}
.analysis-catalog-entry {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) auto;
  align-items: center;
  gap: .65rem;
  min-width: 0;
  padding: .7rem;
  transition: border-color .16s ease, background-color .16s ease;
}
.analysis-catalog-entry:hover { border-color: var(--border-strong); background: var(--surface-raised); }
.analysis-catalog-entry[aria-disabled="true"] { opacity: .68; }
.analysis-catalog-icon {
  display: grid;
  place-items: center;
  width: 2.25rem;
  height: 2.25rem;
  border-radius: .65rem;
  color: var(--accent-hover);
  background: color-mix(in srgb, var(--accent) 14%, transparent);
}
.analysis-catalog-copy { display: grid; gap: .2rem; min-width: 0; }
.analysis-catalog-title { margin: 0; font-size: .92rem; font-weight: 650; overflow-wrap: anywhere; }
.analysis-catalog-title a { color: inherit; text-decoration: none; }
.analysis-catalog-title a:hover { color: var(--accent-hover); }
.analysis-catalog-status, .analysis-catalog-selected { color: var(--muted); font-size: .78rem; line-height: 1.25; overflow-wrap: anywhere; }
.analysis-catalog-selected { color: var(--teal); font-weight: 600; }
.analysis-catalog-action { min-height: 2.35rem; padding: .42rem .6rem; white-space: nowrap; font-size: .78rem; }
.analysis-catalog-action > span { font-size: 1.05rem; }
@media (max-width: 440px) {
  .analysis-catalog-grid { grid-template-columns: 1fr; }
  .analysis-catalog-entry { grid-template-columns: 2.25rem minmax(0, 1fr) auto; }
  .analysis-catalog-action { min-width: 2.75rem; min-height: 2.75rem; }
}
</style>
