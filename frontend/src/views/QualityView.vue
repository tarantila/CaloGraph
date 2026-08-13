<script setup lang="ts">
import {
  PhCalendarX,
  PhCheckCircle,
  PhDatabase,
  PhInfo,
  PhWarningCircle,
} from '@phosphor-icons/vue'
import { computed, onMounted, ref } from 'vue'

import { api, localizeApiError } from '../api'
import StatusBadge from '../components/StatusBadge.vue'
import { formatGermanDate, formatGermanDateTime, shiftIsoDate } from '../date-format'
import { createNumberFormatter, i18n } from '../i18n'
import type { DailyPoint } from '../types'

const t = i18n.global.t.bind(i18n.global)

interface QualityImport {
  id: string
  status: string
  source_type: string
  client_identifier: string | null
  started_at: string
  finished_at: string | null
  received: number
  inserted: number
  updated: number
  skipped: number
  failed: number
  unknown_types: string[]
  error_message: string | null
}

interface Quality {
  start_date: string
  end_date: string
  total_days: number
  recorded_days: number
  coverage_ratio: number
  missing_days: string[]
  incomplete_days: DailyPoint[]
  unknown_types: string[]
  failed_records: number
  imports: QualityImport[]
}

const quality = ref<Quality | null>(null)
const error = ref('')
const loading = ref(true)
const integer = createNumberFormatter({ maximumFractionDigits: 0 })
const percent = createNumberFormatter({ style: 'percent', maximumFractionDigits: 0 })

onMounted(async () => {
  try {
    quality.value = await api<Quality>('/analytics/data-quality')
  } catch (cause) {
    error.value = localizeApiError(cause, 'errors.requestFailed')
  } finally {
    loading.value = false
  }
})

function dateLabel(value: string) {
  return formatGermanDate(value)
}

function sourceLabel(source: string) {
  const keys: Record<string, string> = {
    yazio_export_v1: 'qualityUi.sourceYazio',
    health_auto_export_v2: 'qualityUi.sourceHealthAutoExport',
    calograph_sync_v1: 'qualityUi.sourceCaloGraph',
    apple_health_xml: 'qualityUi.sourceAppleHealth',
    synthetic_demo: 'qualityUi.sourceDemo',
  }
  return keys[source] ? t(keys[source]) : source
}

const missingRanges = computed(() => {
  const dates = quality.value?.missing_days ?? []
  if (!dates.length) return []
  const groups: Array<{ start: string; end: string }> = []
  for (const current of dates) {
    const previous = groups.at(-1)
    if (!previous) {
      groups.push({ start: current, end: current })
      continue
    }
    const expected = shiftIsoDate(previous.end, 1)
    if (expected === current) previous.end = current
    else groups.push({ start: current, end: current })
  }
  return groups.map((group) =>
    group.start === group.end
      ? dateLabel(group.start)
      : `${dateLabel(group.start)} – ${dateLabel(group.end)}`,
  )
})

const rangeLabel = computed(() =>
  quality.value
    ? `${dateLabel(quality.value.start_date)} – ${dateLabel(quality.value.end_date)}`
    : '',
)
const issueImports = computed(() =>
  quality.value?.imports.filter(
    (item) =>
      item.failed > 0 ||
      item.status === 'completed_with_errors' ||
      item.status === 'partial_failed' ||
      item.error_message ||
      item.unknown_types.length,
  ) ?? [],
)
</script>

<template>
  <div class="page-heading">
    <div><h1>{{ t('qualityUi.pageTitle') }}</h1><p>{{ t('qualityUi.pageDescription') }}</p></div>
    <span class="page-context">{{ rangeLabel }}</span>
  </div>

  <div v-if="error" class="card error" role="alert">{{ error }}</div>
  <div v-else-if="loading" class="dashboard-loading">{{ t('qualityUi.loading') }}</div>
  <template v-else-if="quality">
    <section class="insight-strip" :aria-label="t('qualityUi.statsAria')">
      <article class="card insight-card">
        <span class="insight-icon teal"><PhCheckCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('qualityUi.coverage') }}</small><strong>{{ percent.format(quality.coverage_ratio) }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon blue"><PhCalendarX :size="20" weight="duotone" /></span>
        <span><small>{{ t('qualityUi.missingNutrition') }}</small><strong>{{ quality.missing_days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon orange"><PhWarningCircle :size="20" weight="duotone" /></span>
        <span><small>{{ t('qualityUi.missingCalories') }}</small><strong>{{ quality.incomplete_days.length }}</strong></span>
      </article>
      <article class="card insight-card">
        <span class="insight-icon purple"><PhDatabase :size="20" weight="duotone" /></span>
        <span><small>{{ t('qualityUi.issueImports') }}</small><strong>{{ issueImports.length }}</strong></span>
      </article>
    </section>

    <section class="card quality-explainer">
      <span class="quality-explainer-icon"><PhInfo :size="22" weight="fill" /></span>
      <div>
        <h2>{{ t('qualityUi.explainerTitle') }}</h2>
        <p>{{ t('qualityUi.explainerText') }}</p>
      </div>
    </section>

    <div class="quality-detail-grid">
      <section class="card quality-detail-card">
        <div class="section-card-header">
          <div><h2>{{ t('qualityUi.coverage') }}</h2><p>{{ t('qualityUi.coverageDescription', { recorded: quality.recorded_days, total: quality.total_days }) }}</p></div>
        </div>
        <div class="quality-detail-content">
          <progress class="metric-progress quality-coverage-progress" :value="quality.coverage_ratio" max="1">
            {{ percent.format(quality.coverage_ratio) }}
          </progress>
          <template v-if="missingRanges.length">
            <h3>{{ t('qualityUi.missingRanges') }}</h3>
            <ul class="quality-date-list">
              <li v-for="range in missingRanges" :key="range">{{ range }}</li>
            </ul>
            <p class="quality-help">{{ t('qualityUi.missingHelp') }}</p>
          </template>
          <div v-else class="quality-ok"><PhCheckCircle :size="19" weight="fill" /> {{ t('qualityUi.noGaps') }}</div>
        </div>
      </section>

      <section class="card quality-detail-card">
        <div class="section-card-header">
          <div><h2>{{ t('qualityUi.incompleteTitle') }}</h2><p>{{ t('qualityUi.incompleteDescription') }}</p></div>
        </div>
        <div class="quality-day-list">
          <article v-for="day in quality.incomplete_days" :key="day.date">
            <div><strong>{{ dateLabel(day.date) }}</strong><StatusBadge :status="day.tracking_status" /></div>
            <p>{{ day.tracking_reasons.join(' · ') }}</p>
          </article>
          <div v-if="!quality.incomplete_days.length" class="quality-ok"><PhCheckCircle :size="19" weight="fill" /> {{ t('qualityUi.allComplete') }}</div>
        </div>
      </section>
    </div>

    <section class="card table-card">
      <div class="section-card-header">
        <div><h2>{{ t('qualityUi.importsTitle') }}</h2><p>{{ t('qualityUi.importsDescription') }}</p></div>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr><th>{{ t('qualityUi.timestamp') }}</th><th>{{ t('qualityUi.source') }}</th><th>{{ t('qualityUi.status') }}</th><th class="number">{{ t('qualityUi.received') }}</th><th class="number">{{ t('qualityUi.imported') }}</th><th class="number">{{ t('qualityUi.errors') }}</th><th>{{ t('qualityUi.note') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in quality.imports" :key="item.id">
              <td>{{ formatGermanDateTime(item.started_at) }}</td>
              <td><strong>{{ sourceLabel(item.source_type) }}</strong></td>
              <td><StatusBadge :status="item.status" /></td>
              <td class="number">{{ integer.format(item.received) }}</td>
              <td class="number">{{ integer.format(item.inserted + item.updated) }}</td>
              <td class="number">{{ integer.format(item.failed) }}</td>
              <td class="quality-import-note">
                {{ item.error_message || (item.unknown_types.length ? t('qualityUi.unknownTypes', { count: item.unknown_types.length }) : t('qualityUi.noNotes')) }}
              </td>
            </tr>
            <tr v-if="!quality.imports.length"><td colspan="7" class="empty">{{ t('qualityUi.noImports') }}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
