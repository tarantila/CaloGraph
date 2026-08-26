<script setup lang="ts">
import {
  PhArrowsClockwise,
  PhCheckCircle,
  PhDatabase,
  PhInfo,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, localizeApiError } from '../api'
import {
  analyticsPresetMatchesRange,
  inferDesktopPreset,
  parseAnalyticsCompactPreset,
  type AnalyticsCompactPreset,
} from '../analytics-period'
import AnalyticsPeriodFilter from '../components/AnalyticsPeriodFilter.vue'
import { formatGermanDateTime, isoDateInTimeZone, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import { useAuthStore } from '../stores/auth'

const t = i18n.global.t.bind(i18n.global)
type NutrientStatus =
  | 'no_data'
  | 'insufficient_data'
  | 'below_orientation'
  | 'covered'

interface Nutrient {
  id: string
  metric_type: string
  label: string
  category: 'vitamin' | 'mineral'
  unit: 'mg' | 'ug'
  eu_nrv: number | null
  total: number | null
  average_daily: number | null
  days_with_value: number
  coverage_ratio: number
  percent_of_nrv: number | null
  status: NutrientStatus
}

interface MicronutrientResponse {
  start_date: string
  end_date: string
  source: string | null
  recorded_days: number
  last_updated_at: string | null
  available_sources: Array<{ source_type: string; last_updated_at: string | null }>
  nutrients: Nutrient[]
  definition?: {
    coverage_threshold: number
    orientation_threshold_percent: number
  }
}

const route = useRoute()
const router = useRouter()
const today = isoDateInTimeZone(useAuthStore().user?.timezone ?? 'UTC')
const before = shiftIsoDate(today, -29)
const start = ref(String(route.query.start ?? before))
const end = ref(String(route.query.end ?? today))
const source = ref(String(route.query.source ?? 'yazio_export_v1'))
const compactPresets: AnalyticsCompactPreset[] = ['7', '30', '60', 'all']
const periodCandidate = parseAnalyticsCompactPreset(route.query.period, compactPresets)
const period = ref<AnalyticsCompactPreset | undefined>(
  analyticsPresetMatchesRange(periodCandidate, route.query.start, route.query.end)
    ? periodCandidate
    : undefined,
)
const desktopPreset = computed(() => inferDesktopPreset(start.value, end.value))
const result = ref<MicronutrientResponse | null>(null)
const error = ref('')
const loading = ref(true)
const syncingHistory = ref(false)
const syncMessage = ref('')
const syncError = ref('')

function selectPeriod(value: AnalyticsCompactPreset) {
  period.value = value
}

const number = createNumberFormatter({ maximumFractionDigits: 2 })
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const percent = createNumberFormatter({ style: 'percent', maximumFractionDigits: 0 })
const referencePercent = createNumberFormatter({ maximumFractionDigits: 1 })

const sourceLabels: Record<string, string> = {
  yazio_export_v1: 'micronutrientsUi.sourceYazio',
  health_auto_export_v2: 'micronutrientsUi.sourceHealthAutoExport',
  calograph_sync_v1: 'micronutrientsUi.sourceCaloGraph',
  apple_health_xml: 'micronutrientsUi.sourceAppleHealth',
}

function sourceLabel(value: string) {
  return sourceLabels[value] ? t(sourceLabels[value]) : value
}

async function load(allowSourceFallback = true) {
  error.value = ''
  loading.value = true
  await router.replace({
    query: {
      start: start.value,
      end: end.value,
      source: source.value || undefined,
      period: period.value || undefined,
    },
  })
  try {
    const params = new URLSearchParams({ start: start.value, end: end.value })
    if (source.value) params.set('source', source.value)
    if (period.value === 'all') params.set('period', 'all')
    const response = await api<MicronutrientResponse>(`/analytics/micronutrients?${params}`)
    const selectedSourceExists = response.available_sources.some(
      (item) => item.source_type === source.value,
    )
    if (allowSourceFallback && !selectedSourceExists && response.available_sources.length) {
      source.value = response.available_sources[0].source_type
      await load(false)
      return
    }
    result.value = response
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const available = computed(
  () => result.value?.nutrients.filter((item) => item.average_daily != null) ?? [],
)
const reliable = computed(
  () => available.value.filter((item) => item.coverage_ratio >= 0.7),
)
const belowOrientation = computed(
  () => available.value.filter((item) => item.status === 'below_orientation'),
)
const vitamins = computed(
  () => result.value?.nutrients.filter((item) => item.category === 'vitamin') ?? [],
)
const minerals = computed(
  () => result.value?.nutrients.filter((item) => item.category === 'mineral') ?? [],
)
const freshness = computed(() => {
  if (!result.value?.last_updated_at) return t('micronutrientsUi.noDataYet')
  return t('micronutrientsUi.updated', { date: formatGermanDateTime(result.value.last_updated_at) })
})

function unitLabel(unit: Nutrient['unit']) {
  return unit === 'ug' ? 'µg' : unit
}

function amountLabel(item: Nutrient) {
  return item.average_daily == null
    ? '–'
    : `${number.format(item.average_daily)} ${unitLabel(item.unit)}`
}

function referenceAmountLabel(item: Nutrient) {
  return item.eu_nrv == null
    ? t('micronutrientsUi.noReference')
    : `${t('micronutrientsUi.referenceValue')}: ${number.format(item.eu_nrv)} ${unitLabel(item.unit)}`
}

function statusLabel(item: Nutrient) {
  if (item.status === 'no_data') return t('micronutrientsUi.noData')
  if (item.status === 'insufficient_data') return t('micronutrientsUi.insufficient')
  if (item.status === 'below_orientation') return t('micronutrients.below')
  return t('micronutrientsUi.orientationReached')
}

function nrvLabel(item: Nutrient) {
  if (item.eu_nrv == null) return t('micronutrientsUi.noNrv')
  if (item.percent_of_nrv == null) return '–'
  if (item.percent_of_nrv > 0 && item.percent_of_nrv < 0.1) return t('micronutrientsUi.belowSmall')
  return `${referencePercent.format(item.percent_of_nrv)} %`
}

function requiredCoverageDays() {
  const recordedDays = result.value?.recorded_days ?? 0
  const threshold = result.value?.definition?.coverage_threshold ?? 0.7
  return Math.ceil(recordedDays * threshold)
}

function coverageLabel(item: Nutrient) {
  const recordedDays = result.value?.recorded_days ?? 0
  if (!recordedDays) return t('micronutrientsUi.noNutrientsDays')
  const coverage = percent.format(item.coverage_ratio)
  const base = t('micronutrientsUi.coverage', { count: item.days_with_value, total: recordedDays, percent: coverage })
  const requiredDays = requiredCoverageDays()
  return item.days_with_value < requiredDays
    ? t('micronutrientsUi.coverageRequired', { base, required: requiredDays })
    : base
}

async function syncYazioHistory() {
  syncingHistory.value = true
  syncMessage.value = ''
  syncError.value = ''
  try {
    const summary = await api<{ inserted: number; updated: number; skipped: number }>(
      '/yazio/sync?days=60',
      { method: 'POST' },
    )
    syncMessage.value = t('micronutrientsUi.syncSuccess', { count: integer.format(summary.inserted + summary.updated) })
    await load()
  } catch (cause) {
    syncError.value = localizeApiError(cause, 'micronutrientsUi.syncFailed')
  } finally {
    syncingHistory.value = false
  }
}
</script>
<template>
  <div class="page-heading analytics-page-heading micronutrient-heading">
    <div class="analytics-page-heading-content">
      <div>
        <h1>{{ t('micronutrients.title') }}</h1>
        <p>{{ t('micronutrients.description') }}</p>
      </div>
      <AnalyticsPeriodFilter
        v-model:start="start"
        v-model:end="end"
        :initial-preset="desktopPreset"
        :compact-presets="compactPresets"
        :compact-preset="period"
        @preset="selectPeriod"
        @apply="load()"
      />
    </div>
  </div>

  <section class="card filter-panel micronutrient-source-filter" :aria-label="t('micronutrients.chooseSource')">
    <label class="field">
      {{ t('micronutrients.chooseSource') }}
      <select v-model="source" @change="load()">
        <option
          v-for="item in result?.available_sources ?? []"
          :key="item.source_type"
          :value="item.source_type"
        >
          {{ sourceLabel(item.source_type) }}
        </option>
        <option
          v-if="!result?.available_sources.some((item) => item.source_type === source)"
          :value="source"
        >
          {{ sourceLabel(source) }}
        </option>
      </select>
    </label>
    <span class="source-freshness">{{ freshness }}</span>
  </section>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('micronutrientsUi.loading') }}</div>
  <template v-else-if="result">
    <section class="insight-strip" :aria-label="t('micronutrients.stats')">
      <article class="card insight-card">
        <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>{{ t('micronutrients.recorded') }}</small><strong>{{ available.length }} / {{ result.nutrients.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('micronutrients.sufficient') }}</small><strong>{{ reliable.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('micronutrients.below') }}</small><strong>{{ belowOrientation.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>{{ t('micronutrients.nutritionDays') }}</small><strong>{{ result.recorded_days }}</strong></span>
      </article>
    </section>

    <div class="micronutrient-columns">
      <section class="card micronutrient-card">
        <div class="section-card-header">
          <div><h2>{{ t('micronutrients.vitamins') }}</h2><p>{{ t('micronutrients.averagePerDay') }}</p></div>
        </div>
        <div class="nutrient-list">
          <article v-for="item in vitamins" :key="item.id" class="nutrient-row">
            <div class="nutrient-row-top">
              <div><strong>{{ item.label }}</strong><small>{{ referenceAmountLabel(item) }}</small></div>
              <div class="nutrient-value"><strong>{{ amountLabel(item) }}</strong><small>{{ t('micronutrientsUi.averagePerDay') }}</small></div>
            </div>
            <div v-if="item.percent_of_nrv != null" class="nutrient-progress-heading">
              <span>{{ t('micronutrientsUi.referenceShare') }}</span>
              <strong>{{ nrvLabel(item) }}</strong>
            </div>
            <progress
              v-if="item.percent_of_nrv != null"
              :class="['nutrient-progress', item.status]"
              :value="Math.min(item.percent_of_nrv ?? 0, 150)"
              max="150"
              :aria-label="t('micronutrientsUi.referenceAria', { label: item.label, value: nrvLabel(item) })"
            >
              {{ nrvLabel(item) }}
            </progress>
            <div class="nutrient-row-meta">
              <span :class="['nutrient-status', item.status]">{{ statusLabel(item) }}</span>
              <span>{{ coverageLabel(item) }}</span>
            </div>
          </article>
        </div>
      </section>

      <section class="card micronutrient-card">
        <div class="section-card-header">
          <div><h2>{{ t('micronutrients.minerals') }}</h2><p>{{ t('micronutrients.averagePerDay') }}</p></div>
        </div>
        <div class="nutrient-list">
          <article v-for="item in minerals" :key="item.id" class="nutrient-row">
            <div class="nutrient-row-top">
              <div><strong>{{ item.label }}</strong><small>{{ referenceAmountLabel(item) }}</small></div>
              <div class="nutrient-value"><strong>{{ amountLabel(item) }}</strong><small>{{ t('micronutrientsUi.averagePerDay') }}</small></div>
            </div>
            <div v-if="item.percent_of_nrv != null" class="nutrient-progress-heading">
              <span>{{ t('micronutrientsUi.referenceShare') }}</span>
              <strong>{{ nrvLabel(item) }}</strong>
            </div>
            <progress
              v-if="item.percent_of_nrv != null"
              :class="['nutrient-progress', item.status]"
              :value="Math.min(item.percent_of_nrv ?? 0, 150)"
              max="150"
              :aria-label="t('micronutrientsUi.referenceAria', { label: item.label, value: nrvLabel(item) })"
            >
              {{ nrvLabel(item) }}
            </progress>
            <div class="nutrient-row-meta">
              <span :class="['nutrient-status', item.status]">{{ statusLabel(item) }}</span>
              <span>{{ coverageLabel(item) }}</span>
            </div>
          </article>
        </div>
      </section>
    </div>

    <section class="card quality-explainer micronutrient-explainer">
      <span class="quality-explainer-icon"><PhInfo :size="22" weight="fill" /></span>
      <div>
        <h2>{{ t('micronutrientsUi.explainerTitle') }}</h2>
        <p>{{ t('micronutrientsUi.explainerP1') }}</p>
        <p>{{ t('micronutrientsUi.explainerP2', { days: result.recorded_days, required: requiredCoverageDays() }) }}</p>
        <p>{{ t('micronutrientsUi.explainerP3') }}</p>
        <a href="https://eur-lex.europa.eu/legal-content/DE-EN/ALL/?uri=CELEX:32011R1169" target="_blank" rel="noreferrer">{{ t('micronutrientsUi.referenceLink') }}</a>
        <div v-if="source === 'yazio_export_v1'" class="micronutrient-backfill">
          <p>{{ t('micronutrientsUi.backfillHelp') }}</p>
          <button class="button secondary" type="button" :disabled="syncingHistory" @click="syncYazioHistory">
            <PhArrowsClockwise :size="16" weight="bold" aria-hidden="true" />
            {{ syncingHistory ? t('micronutrientsUi.syncLoading') : t('micronutrientsUi.syncButton') }}
          </button>
          <small v-if="syncMessage" class="micronutrient-sync-message">{{ syncMessage }}</small>
          <small v-if="syncError" class="micronutrient-sync-message error">{{ syncError }}</small>
        </div>
      </div>
    </section>

    <p class="micronutrient-source-note">
      {{ t('micronutrientsUi.sourceNote', { source: sourceLabel(result.source ?? source) }) }}
    </p>
  </template>
</template>
